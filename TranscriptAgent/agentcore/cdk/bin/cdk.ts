#!/usr/bin/env node
import * as fs from 'fs';
import * as path from 'path';
import { App } from 'aws-cdk-lib';
import { readAgentCoreProject, resolveTargetStacks, transformAgentCoreJson } from '@aws/agentcore-cdk';
import { AgentCoreStack } from '../lib/cdk-stack';

/** The runtimes' code folders (under app/) that import the archive library. */
const ARCHIVE_USERS = ['agent', 'chatbot'];

/**
 * The CodeZip packager installs each runtime's dependencies as wheels only, so it can't build the archive library
 * from its folder. Copy its `meeting_transcript` package into each runtime's code while synthesizing (the packager
 * zips the code directory as it is), then remove the copies so local runs keep using the editable install.
 */
function vendorArchivePackage(projectRoot: string): () => void {
  const source = path.resolve(projectRoot, '..', 'public_meeting_transcript_archive', 'meeting_transcript');
  if (!fs.existsSync(source)) {
    throw new Error(`The archive package was not found at ${source}`);
  }
  const targets = ARCHIVE_USERS.map((name) => path.join(projectRoot, 'app', name, 'meeting_transcript'));
  const remove = () => targets.forEach((target) => fs.rmSync(target, { recursive: true, force: true }));
  remove();
  for (const target of targets) {
    fs.cpSync(source, target, {
      recursive: true,
      filter: (file) => {
        const rel = path.relative(source, file).split(path.sep);
        return !rel.includes('tests') && !rel.includes('__pycache__') && !rel.includes('.gitignore');
      },
    });
  }
  return remove;
}

let cleanup = () => {};
try {
  // The AgentCore CLI runs this app from agentcore/cdk/; readAgentCoreProject walks up to agentcore/.
  const project = readAgentCoreProject();
  cleanup = vendorArchivePackage(project.projectRoot);
  const app = new App();
  for (const stack of resolveTargetStacks({
    projectName: project.projectName,
    targets: project.targets,
    deployedState: project.deployedState,
  })) {
    new AgentCoreStack(app, stack.stackName, {
      env: stack.env,
      tags: stack.tags,
      description: stack.description,
      application: transformAgentCoreJson(project.agentCoreJson, {
        projectRoot: project.projectRoot,
        credentials: stack.credentials,
        target: stack.target,
      }),
    });
  }
  app.synth();
  cleanup();
} catch (error) {
  cleanup();
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exit(1);
}
