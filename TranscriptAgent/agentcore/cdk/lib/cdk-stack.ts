import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import { Stack, type StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as path from 'path';
import { WebFrontend } from './web-frontend';

export interface AgentCoreStackProps extends StackProps {
  /** Props for the AgentCore application, produced by transformAgentCoreJson from agentcore/agentcore.json. */
  application: AgentCoreApplicationProps;
}

/** The read-only chatbot declared in agentcore/agentcore.json; the website's signed-in viewers talk to it. */
const CHATBOT_RUNTIME_NAME = 'chatbot';
/** The existing resources the website uses: the same ones agentcore/agentcore.json gives the runtimes. */
const DATABASE_SECRET_NAME = 'TranscriptionDBSecret';
const MEETING_BUCKET_NAME = 'lg-transcription-bucket';
const ARCHIVE_POLICY_NAME = 'meeting-transcript-archive';
/** The website's own secret, made by hand in Secrets Manager (see TranscriptAgent/website/README.md). */
const WEB_SECRET_NAME = 'TranscriptArchiveWebSecret';
/**
 * Which version of that secret CloudFront and Django both use: the VersionId that `create-secret` or
 * `put-secret-value` printed. After changing the secret, put its new VersionId here and deploy; until then the site
 * keeps using the old value, so nothing breaks. Empty means the current version, which a change wouldn't reach.
 */
const WEB_SECRET_VERSION = '';

/**
 * The stack the AgentCore CLI deploys. Everything declared in agentcore/agentcore.json (both runtimes, their settings
 * and IAM policies, and the Memory) is created by the AgentCoreApplication construct. The runtimes use the existing
 * database, secret and S3 bucket named there.
 *
 * The only thing added here is the website (see web-frontend.ts): Django in a Lambda behind CloudFront.
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    this.application = new AgentCoreApplication(this, 'Application', props.application);

    // The website: the clerks' admin, the chatbot page for signed-in viewers, and the public read-only API
    const chatbot = this.application.runtime(CHATBOT_RUNTIME_NAME);
    const web = new WebFrontend(this, 'Web', {
      chatbotRuntimeArn: chatbot.runtimeArn,
      websiteDir: path.join(props.application.projectRoot, 'website'),
      archiveDir: path.join(props.application.projectRoot, '..', 'public_meeting_transcript_archive'),
      databaseSecretName: DATABASE_SECRET_NAME,
      meetingBucketName: MEETING_BUCKET_NAME,
      archivePolicyName: ARCHIVE_POLICY_NAME,
      webSecretName: WEB_SECRET_NAME,
      webSecretVersion: WEB_SECRET_VERSION,
    });
    chatbot.grantInvoke(web.siteFunction);
  }
}
