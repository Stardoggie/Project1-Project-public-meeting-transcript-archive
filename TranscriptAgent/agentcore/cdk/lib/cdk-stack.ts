import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import { Stack, type StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as path from 'path';
import { WebFrontend } from './web-frontend';

export interface AgentCoreStackProps extends StackProps {
  /** Props for the AgentCore application, produced by transformAgentCoreJson from agentcore/agentcore.json. */
  application: AgentCoreApplicationProps;
}

/** The read-only chatbot declared in agentcore/agentcore.json; the web page only ever talks to it. */
const CHATBOT_RUNTIME_NAME = 'chatbot';

/**
 * The stack the AgentCore CLI deploys. Everything declared in agentcore/agentcore.json (both runtimes, their settings
 * and IAM policies, and the Memory) is created by the AgentCoreApplication construct. The runtimes use the existing
 * database, secret and S3 bucket named there.
 *
 * The only thing added here is the hosted web page (see web-frontend.ts), wired to the `chatbot`.
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    this.application = new AgentCoreApplication(this, 'Application', props.application);

    // The hosted web page (CloudFront, and the /api function that calls the chatbot)
    const chatbot = this.application.runtime(CHATBOT_RUNTIME_NAME);
    const web = new WebFrontend(this, 'Web', {
      runtimeArn: chatbot.runtimeArn,
      webDir: path.join(props.application.projectRoot, 'web'),
    });
    chatbot.grantInvoke(web.apiFunction);
  }
}
