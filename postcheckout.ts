const URL_PREFIX = "git@github.com:twin-so/";
const REF_PREFIX = `ref: refs/heads/`;
const DECODER = new TextDecoder();

async function git(args) {
  const command = new Deno.Command('git', {
    args
  });
  const { code, stdout, stderr } = await command.output();

  if (code !== 0) {
    const message = stderr.length
      ? DECODER.decode(stderr).trim()
      : 'Unknown error retrieving origin URL';
    throw new Error(message);
  }

  return DECODER.decode(stdout).trim();
}

function readApiKey() {
  const home = Deno.env.get('HOME');
  const path = `${home}/.linear`;
  return Deno.readTextFileSync(path).trim();
}

async function queryLinear(apiKey: string, query: string, variables: Object | undefined) {
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
  const gitDir = Deno.env.get('GIT_DIR') ?? '.git';

  if (Deno.args.length == 0) {
    if ((await Deno.stat(gitDir)).isDirectory) {
      const path = `${gitDir}/hooks/post-checkout`;
      try { await Deno.removeSync(path); } catch {}
      await Deno.symlink(Deno.env.get('SELF'), path);
    } else {
      throw new Error('Not in a git repository');
    }
    return;
  }

  if (Deno.args[2] !== '1') {
    return;
  }

  const next = Deno.readTextFileSync(`${gitDir}/HEAD`).trim();
  const user = Deno.env.get('USER');

  if (!next.startsWith(`${REF_PREFIX}${user}/`)) {
    return;
  }

  const branch = next.substring(REF_PREFIX.length);

  const remoteUrl = await git(['remote', 'get-url', 'origin']);

  if (!remoteUrl.startsWith(URL_PREFIX)) {
    return;
  }

  const project = remoteUrl.substring(URL_PREFIX.length);

  const apiKey = readApiKey();

  const existing = (await queryLinear(apiKey, `
    query ($branch:String!) {
      issues(
        filter: {
          assignee: { isMe: { eq: true } }
          title: { containsIgnoreCase: $branch }
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
    throw new Error(`Team ${teamName} not found`);
  }

  const stateId = data.workflowStates.nodes.find((s) => s.name === 'In Progress' && s.team.id === teamId)?.id;

  if (!stateId) {
    throw new Error(`State In Progress not found`);
  }

  const title = `[${project}] ${branch.substring(user.length + 1)}`;

  const issue = (await queryLinear(apiKey, `
    mutation ($assigneeId:String! $teamId:String! $stateId:String! $title:String!) {
      issueCreate(
        input: {
          teamId: $teamId
          assigneeId: $assigneeId
          stateId: $stateId
          title: $title
          description: "Created automatically for ${branch}."
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
    Deno.exit(1);
  });
}
