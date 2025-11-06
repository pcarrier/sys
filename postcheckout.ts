import { promises as fs } from 'node:fs';

const URL_PREFIX = "git@github.com:twin-so/";
const REF_PREFIX = `ref: refs/heads/`;
const DECODER = new TextDecoder();

async function git(args: string[]): Promise<string> {
  const result = Bun.spawnSync({
    cmd: ['git', ...args],
  });

  if (result.exitCode !== 0) {
    const message = result.stderr && result.stderr.length
      ? DECODER.decode(result.stderr).trim()
      : 'Unknown error retrieving origin URL';
    throw new Error(message);
  }

  return result.stdout ? DECODER.decode(result.stdout).trim() : '';
}

async function readApiKey() {
  const home = process.env['HOME'];
  if (!home) {
    throw new Error('HOME environment variable is not set');
  }
  const path = `${home}/.linear`;
  return (await Bun.file(path).text()).trim();
}

async function queryLinear(apiKey: string, query: string, variables: Record<string, unknown> | undefined = undefined) {
  const body = JSON.stringify({
    query,
    variables,
  });
  const resp = await fetch('https://api.linear.app/graphql', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': apiKey,
    },
    body,
  });
  if (resp.status !== 200) {
    throw new Error(`HTTP ${resp.status}: ${resp.statusText} (${await resp.text()})`);
  }
  const rbody = await resp.json();
  if (rbody.errors) {
    throw new Error(rbody.errors.map((e) => e.message).join('\n'));
  }
  return rbody.data;
}

async function main() {
  const gitDir = process.env['GIT_DIR'] ?? '.git';
  const args = process.argv.slice(2);

  if (args.length === 0) {
    const stats = await fs.stat(gitDir);
    if (stats.isDirectory()) {
      const path = `${gitDir}/hooks/post-checkout`;
      await fs.rm(path, { force: true });
      const self = process.env['SELF'];
      if (!self) {
        throw new Error('SELF environment variable is not set');
      }
      await fs.symlink(self, path);
    } else {
      throw new Error('Not in a git repository');
    }
    return;
  }

  if (args[2] !== '1') {
    return;
  }

  const next = (await Bun.file(`${gitDir}/HEAD`).text()).trim();
  const user = process.env['USER'];

  if (!user || !next.startsWith(`${REF_PREFIX}${user}/`)) {
    return;
  }

  const branch = next.substring(REF_PREFIX.length);

  const remoteUrl = await git(['remote', 'get-url', 'origin']);

  if (!remoteUrl.startsWith(URL_PREFIX)) {
    return;
  }
 
  let project = remoteUrl.substring(URL_PREFIX.length);
  if (project.endsWith('.git')) {
    project = project.substring(0, project.length - 4);
  }

  const apiKey = await readApiKey();

  const existing = (await queryLinear(apiKey, `
    query ($branch:String!) {
      issues(
        filter: {
          assignee: { isMe: { eq: true } }
          description: { contains: $branch }
        }
      ) {
        nodes {
          id
        }
      }
    }
  `, {
    branch
  })).issues.nodes;

  if (existing.length > 0) {
    return;
  }

  const data = (await queryLinear(apiKey, `
    {
      viewer { id }
      teams { nodes { id key } }
      workflowStates { nodes { id name team { id } } }
    }
  `));
  const assigneeId = data.viewer.id;

  const teamKey = await git(['config', 'get', '--default=TWI', 'linear.team']);

  const teamId = data.teams.nodes.find((t) => t.key === teamKey)?.id;

  if (!teamId) {
    throw new Error(`Team ${teamKey} not found`);
  }

  const stateId = data.workflowStates.nodes.find((s) => s.name === 'In Progress' && s.team.id === teamId)?.id;

  if (!stateId) {
    throw new Error(`State In Progress not found`);
  }

  const title = `[${project}] ${branch.substring(user.length + 1).replaceAll('_', ' ')}`;

  const issue = (await queryLinear(apiKey, `
    mutation ($assigneeId:String! $teamId:String! $stateId:String! $title:String!) {
      issueCreate(
        input: {
          teamId: $teamId
          assigneeId: $assigneeId
          stateId: $stateId
          title: $title
          description: "Created automatically for \`${branch}\`."
        }
      ) {
        issue {
          identifier
          url
        }
      }
    }
  `, {
    assigneeId,
    teamId,
    title,
    stateId,
  })).issueCreate.issue;

  console.log(`Created issue ${issue.identifier} @ ${issue.url}`);
}

if (import.meta.main) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
