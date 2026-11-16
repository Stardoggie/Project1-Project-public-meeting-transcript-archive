import { CfnOutput, Duration, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as nodejs from 'aws-cdk-lib/aws-lambda-nodejs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';
import * as path from 'path';

export interface WebFrontendProps {
  /** The agent runtime's ARN; the API function calls it. */
  readonly runtimeArn: string;
  /** The recordings bucket: the browser uploads to it directly, so it gets a CORS rule for the site. */
  readonly meetingBucket: s3.Bucket;
  /** The folder holding index.html (TranscriptAgent/web). */
  readonly webDir: string;
}

/**
 * The hosted front end: TranscriptAgent/web/index.html on CloudFront, from a private bucket. /api/* goes to a streaming
 * Lambda (behind CloudFront origin access control) that calls the runtime.
 *
 * There is no sign-in: anyone with the site's address can use the agent, including changing and deleting archive
 * records and uploading recordings.
 */
export class WebFrontend extends Construct {
  public readonly apiFunction: nodejs.NodejsFunction;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebFrontendProps) {
    super(scope, id);
    const stack = Stack.of(this);

    // API
    this.apiFunction = new nodejs.NodejsFunction(this, 'Api', {
      entry: path.join(__dirname, '..', '..', 'lambda', 'api', 'index.ts'),
      runtime: lambda.Runtime.NODEJS_22_X,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 256,
      // An agent turn can take minutes; the response streams, so CloudFront waits as long as bytes keep coming
      timeout: Duration.minutes(5),
      bundling: {
        // Bundle the AWS SDK too: the Lambda runtime's copy may not have the bedrock-agentcore client yet
        externalModules: [],
        minify: true,
        sourceMap: true,
      },
      environment: {
        RUNTIME_ARN: props.runtimeArn,
        NODE_OPTIONS: '--enable-source-maps',
      },
    });
    const apiUrl = this.apiFunction.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    // Site
    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    this.distribution = new cloudfront.Distribution(this, 'Site', {
      comment: 'Meeting Transcript Archive',
      defaultRootObject: 'index.html',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        // Two small files: always serve the latest
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: origins.FunctionUrlOrigin.withOriginAccessControl(apiUrl, { readTimeout: Duration.seconds(60) }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        },
      },
    });
    // Function URLs created since late 2025 need lambda:InvokeFunction as well as the InvokeFunctionUrl that the
    // origin access control grants
    this.apiFunction.addPermission('CloudFrontInvokeFunction', {
      principal: new iam.ServicePrincipal('cloudfront.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: this.distribution.distributionArn,
    });
    const siteUrl = `https://${this.distribution.distributionDomainName}`;

    new s3deploy.BucketDeployment(this, 'SiteContent', {
      sources: [
        s3deploy.Source.asset(props.webDir, { exclude: ['*', '!index.html'] }),
        // Tells the page it's hosted (local server.py has no config.json), so it signs request bodies for CloudFront
        s3deploy.Source.jsonData('config.json', { hosted: true }),
      ],
      destinationBucket: siteBucket,
      distribution: this.distribution,
      distributionPaths: ['/*'],
    });

    // The page PUTs recordings straight to the upload link the agent hands out
    props.meetingBucket.addCorsRule({
      allowedMethods: [s3.HttpMethods.PUT],
      allowedOrigins: [siteUrl],
      allowedHeaders: ['*'],
      maxAge: 3000,
    });

    new CfnOutput(stack, 'SiteUrl', { value: siteUrl });
  }
}
