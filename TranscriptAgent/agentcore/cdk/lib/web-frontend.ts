import { AssetHashType, CfnOutput, Duration, SecretValue, Stack } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { execFileSync } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { Construct } from 'constructs';

export interface WebFrontendProps {
  /** The chatbot runtime's ARN; signed-in viewers' questions go to it. */
  readonly chatbotRuntimeArn: string;
  /** The Django project (TranscriptAgent/website). */
  readonly websiteDir: string;
  /** The archive library (public_meeting_transcript_archive), bundled with the site. */
  readonly archiveDir: string;
  /** The existing database secret's name, the same one the runtimes use. */
  readonly databaseSecretName: string;
  /** The existing recordings bucket. */
  readonly meetingBucketName: string;
  /** The existing IAM managed policy for S3, Transcribe, Comprehend and the database secret (the clerks' agent's). */
  readonly archivePolicyName: string;
  /** The website's own secret, made by hand: { "django_secret_key": "...", "origin_verify": "..." }. */
  readonly webSecretName: string;
  /** The version of that secret CloudFront and Django both read; empty for the current version. */
  readonly webSecretVersion: string;
}

/** AWS Lambda Web Adapter: runs gunicorn inside Lambda and streams its responses. https://github.com/aws/aws-lambda-web-adapter */
const WEB_ADAPTER_LAYER = 'arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerX86:30';
const GUNICORN_PORT = '8000';
/** What the bundle needs from each folder (no virtualenvs, caches, tests or local static files). */
const SKIPPED = ['.venv', '__pycache__', '.pytest_cache', 'tests', 'staticfiles', '.env'];

/** A hash of every file the bundle is made from, so a change in the website or the archive redeploys the Lambda. */
function hashTree(roots: string[]): string {
  const hash = crypto.createHash('sha256');
  const visit = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (SKIPPED.includes(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        visit(full);
      } else {
        hash.update(full);
        hash.update(fs.readFileSync(full));
      }
    }
  };
  roots.forEach(visit);
  return hash.digest('hex');
}

function copyTree(source: string, target: string) {
  fs.cpSync(source, target, {
    recursive: true,
    filter: (file) => !path.relative(source, file).split(path.sep).some((part) => SKIPPED.includes(part)),
  });
}

/**
 * Builds the Lambda's code on this machine, with uv and without Docker: the locked dependencies as Linux wheels,
 * the archive's meeting_transcript package, the Django project, its collected static files, and run.sh.
 */
function bundleWebsite(websiteDir: string, archiveDir: string, outputDir: string): boolean {
  const requirements = path.join(outputDir, 'requirements.txt');
  execFileSync('uv', ['export', '--project', websiteDir, '--frozen', '--no-dev', '--no-hashes', '--no-emit-project',
    '--no-emit-package', 'public-meeting-transcript-archive', '-o', requirements], { stdio: 'inherit' });
  execFileSync('uv', ['pip', 'install', '--target', outputDir, '--python-platform', 'x86_64-manylinux2014',
    '--python-version', '3.14', '--only-binary', ':all:', '-r', requirements], { stdio: 'inherit' });
  fs.rmSync(requirements);
  copyTree(path.join(archiveDir, 'meeting_transcript'), path.join(outputDir, 'meeting_transcript'));
  for (const name of ['manage.py', 'archive_site', 'records', 'chat', 'public_api', 'templates']) {
    copyTree(path.join(websiteDir, name), path.join(outputDir, name));
  }
  // collectstatic only copies files, but the settings it loads want a database URL and a key; these are never used
  execFileSync('uv', ['run', '--project', websiteDir, 'python', 'manage.py', 'collectstatic', '--noinput'], {
    cwd: websiteDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      DJANGO_STATIC_ROOT: path.join(outputDir, 'staticfiles'),
      DATABASE_URL: 'postgresql://collectstatic@localhost/unused',
      DJANGO_DEBUG: '1',
      WEB_SECRET_ID: '',
      AWS_REGION: 'us-east-1',
      MEETING_BUCKET_NAME: 'unused',
    },
  });
  fs.writeFileSync(path.join(outputDir, 'run.sh'),
    '#!/bin/bash\n' +
    '# Started by the Lambda Web Adapter; it forwards each request to gunicorn and streams the answer back\n' +
    `exec python3 -m gunicorn --bind 127.0.0.1:${GUNICORN_PORT} --workers 1 --threads 8 --timeout 300 ` +
    'archive_site.wsgi:application\n', { mode: 0o755 });
  return true;
}

/**
 * The website: Django (TranscriptAgent/website) in one Lambda, behind CloudFront.
 * - Clerks sign in to /admin/ to manage the archive, upload recordings and run transcriptions.
 * - Viewers (accounts the clerks make) sign in to /chat/ to ask the read-only chatbot.
 * - Anyone can read the archive through /api/v1/, which never calls an agent.
 *
 * The Lambda's function URL is public, because a URL behind origin access control needs every POST body signed by
 * the browser, which plain HTML forms can't do. CloudFront adds a secret X-Origin-Verify header instead (read from
 * the website's secret at deploy time), and Django refuses requests without it.
 */
export class WebFrontend extends Construct {
  public readonly siteFunction: lambda.Function;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebFrontendProps) {
    super(scope, id);
    const stack = Stack.of(this);
    const webSecret = secretsmanager.Secret.fromSecretNameV2(this, 'WebSecret', props.webSecretName);

    this.siteFunction = new lambda.Function(this, 'Django', {
      description: 'The archive website (Django)',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.X86_64,
      handler: 'run.sh',
      code: lambda.Code.fromAsset(props.websiteDir, {
        assetHashType: AssetHashType.CUSTOM,
        assetHash: hashTree([props.websiteDir, props.archiveDir]),
        bundling: {
          image: lambda.Runtime.PYTHON_3_14.bundlingImage,
          local: { tryBundle: (outputDir) => bundleWebsite(props.websiteDir, props.archiveDir, outputDir) },
        },
      }),
      layers: [lambda.LayerVersion.fromLayerVersionArn(this, 'WebAdapter', WEB_ADAPTER_LAYER)],
      memorySize: 1024,
      // A chatbot answer, or the analysis after a transcription finishes, can take a few minutes
      timeout: Duration.minutes(5),
      environment: {
        AWS_LAMBDA_EXEC_WRAPPER: '/opt/bootstrap',
        AWS_LWA_PORT: GUNICORN_PORT,
        AWS_LWA_INVOKE_MODE: 'response_stream',
        DATABASE_SECRET_ID: props.databaseSecretName,
        MEETING_BUCKET_NAME: props.meetingBucketName,
        WEB_SECRET_ID: props.webSecretName,
        WEB_SECRET_VERSION: props.webSecretVersion,
        CHATBOT_RUNTIME_ARN: props.chatbotRuntimeArn,
      },
    });
    this.siteFunction.role!.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyName(this, 'ArchivePolicy', props.archivePolicyName));
    webSecret.grantRead(this.siteFunction);
    const siteUrl = this.siteFunction.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    // The Lambda is called by its function URL, so pass the site's own address on for Django's host and CSRF checks
    const forwardHost = new cloudfront.Function(this, 'ForwardHost', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        'function handler(event) {\n' +
        '  var request = event.request;\n' +
        '  request.headers["x-forwarded-host"] = { value: request.headers.host.value };\n' +
        '  return request;\n' +
        '}\n'),
    });
    const siteOrigin = new origins.FunctionUrlOrigin(siteUrl, {
      readTimeout: Duration.seconds(60),
      customHeaders: {
        // Pinned to the same version Django reads, so a changed secret only takes effect at the deploy that names it
        'X-Origin-Verify': SecretValue.secretsManager(props.webSecretName, {
          jsonField: 'origin_verify',
          versionId: props.webSecretVersion || undefined,
        }).unsafeUnwrap(),
      },
    });
    const toDjango = {
      origin: siteOrigin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
      functionAssociations: [{ function: forwardHost, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
    };
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Meeting Transcript Archive',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        ...toDjango,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        // Pages depend on who is signed in, so nothing is cached
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      },
      additionalBehaviors: {
        // The admin's CSS and scripts never change between deploys
        '/static/*': { ...toDjango, cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED },
      },
    });

    new CfnOutput(stack, 'SiteUrl', { value: `https://${this.distribution.distributionDomainName}` });
  }
}
