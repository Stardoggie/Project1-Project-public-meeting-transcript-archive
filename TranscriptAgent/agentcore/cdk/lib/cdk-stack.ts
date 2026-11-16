import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import { CfnOutput, CustomResource, Duration, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { execFileSync } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { WebFrontend } from './web-frontend';

export interface AgentCoreStackProps extends StackProps {
  /** Props for the AgentCore application, produced by transformAgentCoreJson from agentcore/agentcore.json. */
  application: AgentCoreApplicationProps;
}

/** The runtime declared in agentcore/agentcore.json. */
const RUNTIME_NAME = 'agent';

/**
 * AgentCore Runtime only places VPC-mode runtimes in some Availability Zones, given as AZ IDs (AZ names map to
 * different zones in every account). Check
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html before deploying to a new region.
 */
const AGENTCORE_AZ_IDS: Record<string, string[]> = {
  'us-east-1': ['use1-az1', 'use1-az2'],
};

/** Python the migration Lambda runs and its dependencies are installed for. */
const MIGRATE_PYTHON = { runtime: lambda.Runtime.PYTHON_3_13, version: '3.13', platform: 'aarch64-manylinux2014' };

/** A hash of every file under `roots`, so the migration Lambda is rebuilt, and rerun, when any of them change. */
function hashTree(roots: string[]): string {
  const skip = new Set(['__pycache__', 'tests', '.venv', 'build', '.pytest_cache']);
  const hash = crypto.createHash('sha256');
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (skip.has(entry.name) || entry.name.endsWith('.egg-info')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else {
        hash.update(path.relative(dir, full));
        hash.update(fs.readFileSync(full));
      }
    }
  };
  roots.forEach(walk);
  return hash.digest('hex');
}

/**
 * The stack the AgentCore CLI deploys. The AgentCoreApplication creates what agentcore/agentcore.json declares
 * (the `agent` runtime and its Memory); this stack adds what the archive needs around it:
 *
 * - a VPC (public subnets with one NAT gateway, private subnets for the runtime and the migration Lambda,
 *   isolated subnets for the database), and the runtime switched to VPC mode
 * - PostgreSQL on RDS, its credentials in Secrets Manager
 * - the S3 bucket for recordings (`audio/`) and Transcribe output (`transcripts/`)
 * - the runtime's permissions for S3, Transcribe, Comprehend and the database secret
 * - a custom resource that runs the archive's Alembic migrations inside the VPC on deploy
 * - the hosted front end (see web-frontend.ts)
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    this.application = new AgentCoreApplication(this, 'Application', props.application);
    const archiveDir = path.resolve(props.application.projectRoot, '..', 'public_meeting_transcript_archive');

    // Network
    const azIds = AGENTCORE_AZ_IDS[this.region];
    const vpc = new ec2.Vpc(this, 'Network', {
      // The names are placeholders when azIds is set: each subnet is pinned to an AZ ID below
      ...(azIds ? { availabilityZones: azIds.map((_, i) => `${this.region}${'abcdef'[i]}`) } : { maxAzs: 2 }),
      natGateways: 1,
      subnetConfiguration: [
        { name: 'Public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'App', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 22 },
        { name: 'Data', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });
    if (azIds) {
      for (const subnet of [...vpc.publicSubnets, ...vpc.privateSubnets, ...vpc.isolatedSubnets]) {
        const cfnSubnet = subnet.node.defaultChild as ec2.CfnSubnet;
        cfnSubnet.availabilityZone = undefined;
        cfnSubnet.availabilityZoneId = azIds[vpc.availabilityZones.indexOf(subnet.availabilityZone)];
      }
    }
    const appSubnets = vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS });
    const runtimeSecurityGroup = new ec2.SecurityGroup(this, 'RuntimeSecurityGroup', {
      vpc,
      description: 'Transcript agent runtime',
    });
    const migrateSecurityGroup = new ec2.SecurityGroup(this, 'MigrateSecurityGroup', {
      vpc,
      description: 'Archive database migrations',
    });

    // Database
    const database = new rds.DatabaseInstance(this, 'Database', {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16_13 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      credentials: rds.Credentials.fromGeneratedSecret('archive_admin'),
      databaseName: 'transcript_archive',
      allocatedStorage: 20,
      maxAllocatedStorage: 100,
      storageEncrypted: true,
      multiAz: false,
      publiclyAccessible: false,
      backupRetention: Duration.days(7),
      deletionProtection: false,
      // Deleting the stack leaves a final snapshot of the archive rather than losing it
      removalPolicy: RemovalPolicy.SNAPSHOT,
    });
    database.connections.allowDefaultPortFrom(runtimeSecurityGroup, 'Transcript agent runtime');
    database.connections.allowDefaultPortFrom(migrateSecurityGroup, 'Archive database migrations');
    const databaseSecret = database.secret!;

    // Recordings and transcripts. Kept when the stack is deleted, like the README says S3 files are.
    const bucket = new s3.Bucket(this, 'MeetingBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Migrations: `flask db upgrade` from a Lambda in the VPC, rerun whenever the archive code or migrations change
    const migrateDir = path.join(props.application.projectRoot, 'agentcore', 'cdk', 'lambda', 'migrate');
    const migrateHash = hashTree([
      migrateDir,
      path.join(archiveDir, 'meeting_transcript'),
      path.join(archiveDir, 'migrations'),
    ]);
    const migrateFunction = new lambda.Function(this, 'MigrateFunction', {
      runtime: MIGRATE_PYTHON.runtime,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(migrateDir, {
        assetHash: migrateHash,
        bundling: {
          image: MIGRATE_PYTHON.runtime.bundlingImage,
          local: {
            tryBundle(outputDir: string) {
              // Installs the archive and its dependencies for Linux arm64 with uv, so no Docker is needed
              execFileSync(
                'uv',
                ['pip', 'install', archiveDir, '--target', outputDir, '--python-version', MIGRATE_PYTHON.version,
                  '--python-platform', MIGRATE_PYTHON.platform, '--quiet'],
                { stdio: ['ignore', 'pipe', 'inherit'] },
              );
              fs.cpSync(path.join(archiveDir, 'migrations'), path.join(outputDir, 'migrations'), {
                recursive: true,
                filter: (file) => !file.split(path.sep).includes('__pycache__'),
              });
              fs.copyFileSync(path.join(migrateDir, 'handler.py'), path.join(outputDir, 'handler.py'));
              return true;
            },
          },
        },
      }),
      timeout: Duration.minutes(5),
      memorySize: 512,
      vpc,
      vpcSubnets: appSubnets,
      securityGroups: [migrateSecurityGroup],
      environment: {
        DATABASE_SECRET_ARN: databaseSecret.secretArn,
        MEETING_BUCKET_NAME: bucket.bucketName,
        MEETING_LOG_LEVEL: 'INFO',
      },
    });
    databaseSecret.grantRead(migrateFunction);
    const migrations = new CustomResource(this, 'ArchiveMigrations', {
      serviceToken: new cr.Provider(this, 'MigrateProvider', { onEventHandler: migrateFunction }).serviceToken,
      properties: { CodeHash: migrateHash },
    });
    migrations.node.addDependency(database);

    // The agent runtime
    const runtime = this.application.runtime(RUNTIME_NAME);
    runtime.grantReadWrite(bucket, 'audio/*');
    runtime.grantReadWrite(bucket, 'transcripts/*');
    // Lets a missing object come back as 404 rather than 403
    runtime.grant(['s3:ListBucket'], [bucket.bucketArn]);
    runtime.addToPolicy(
      new iam.PolicyStatement({
        // Transcribe and Comprehend have no resource-level permissions for these actions
        actions: [
          'transcribe:StartTranscriptionJob',
          'transcribe:GetTranscriptionJob',
          'transcribe:DeleteTranscriptionJob',
          'comprehend:DetectEntities',
          'comprehend:DetectKeyPhrases',
        ],
        resources: ['*'],
      }),
    );
    runtime.grantRead(databaseSecret);
    runtime.addEnvironmentVariable('MEETING_BUCKET_NAME', bucket.bucketName);
    runtime.addEnvironmentVariable('DATABASE_SECRET_ARN', databaseSecret.secretArn);
    // agentcore.json's networkConfig only takes literal subnet and security group IDs, so set VPC mode here
    const cfnRuntime = runtime.resource.node.defaultChild as bedrockagentcore.CfnRuntime;
    cfnRuntime.networkConfiguration = {
      networkMode: 'VPC',
      networkModeConfig: {
        subnets: appSubnets.subnetIds,
        securityGroups: [runtimeSecurityGroup.securityGroupId],
      },
    };
    // Update the runtime only once the database schema is current
    runtime.resource.node.addDependency(migrations);

    // The hosted front end (CloudFront, Cognito sign-in, and the /api function that calls the runtime)
    const web = new WebFrontend(this, 'Web', {
      runtimeArn: runtime.runtimeArn,
      meetingBucket: bucket,
      webDir: path.join(props.application.projectRoot, 'web'),
    });
    runtime.grantInvoke(web.apiFunction);

    new CfnOutput(this, 'MeetingBucketName', { value: bucket.bucketName });
    new CfnOutput(this, 'DatabaseSecretArn', { value: databaseSecret.secretArn });
    new CfnOutput(this, 'DatabaseEndpoint', { value: database.dbInstanceEndpointAddress });
  }
}
