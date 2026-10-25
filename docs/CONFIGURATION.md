# Filmot configuration and optional services

[Quick start](../README.md#quick-start) · [CLI reference](CLI_REFERENCE.md) · [Official Filmot API](https://filmot.com/api-documentation)

Use this guide to configure credentials, storage, provider routing, and optional
transcript services. For command syntax and examples, see the
[CLI reference](CLI_REFERENCE.md).

- [API authentication](#api-authentication)
- [API providers, routing, and credits](#api-providers-routing-and-credits)
- [Storage and configuration](#storage-and-configuration)
- [Dependencies](#dependencies)
- [AWS Transcribe fallback](#aws-transcribe-fallback)
- [Webshare proxy pool](#webshare-proxy-pool-transcript-fetching)

## API Authentication

Filmot API operations use `FILMOT_API_KEY` for the
[official Filmot API](https://filmot.com/api-documentation):

1. Create a key at [Filmot API keys](https://filmot.com/api-keys).
2. Set `FILMOT_API_KEY` in your per-user `config.env` (recommended) or project `.env`.
3. Run `filmot config` to check configuration without printing the key.

See [Storage and configuration](#storage-and-configuration) for file locations
and precedence.

`RAPIDAPI_KEY` is only for existing legacy gateway subscriptions. It supports
shared routes but does not enable official-API-only features such as `titles`,
`find-channels`, and `subtitle-export`.

Direct `yt-search`, `yt-video`, `yt-playlist`, `yt-playlists`, `yt-comments`,
`yt-replies`, `yt-data refresh`, and `channel-download` require a restricted
`YOUTUBE_API_KEY`. (`yt-data status` and `yt-data purge` are offline.) They use
public, API-key-authenticated YouTube Data API reads; the key does not grant
caption access. See [YouTube Data API behavior](YOUTUBE_API.md) for quota,
attribution, and metadata lifecycle details.

Native transport/provider errors, typed result errors, discovery artifacts,
and ledger events pass through credential-redaction boundaries. The YouTube
request exceptions deliberately do not retain native request/response objects
or credential-bearing URLs. Redaction recognizes common key/token/signature
fields and URL parameters, but it is defense in depth rather than a substitute
for restricting/rotating keys and keeping raw credentials out of titles,
queries, and free-form notes.

## API providers, routing, and credits

The `direct` provider is Filmot's official API. With only `FILMOT_API_KEY`
configured, the default `auto` policy uses it for every Filmot API request.
Legacy RapidAPI subscribers can additionally set `RAPIDAPI_KEY`; when both
keys are configured, shared routes keep the legacy-first order shown below.

| Policy | Subtitle search, video metadata, channel lookup | Direct-only features |
| --- | --- | --- |
| `auto` (default) | Official API; legacy RapidAPI first if configured | Official API |
| `direct` | Official API | Official API |
| `rapidapi` (legacy) | Legacy RapidAPI | Unavailable, with a clear error |

Choose a policy with `FILMOT_API_PROVIDER=auto|direct|rapidapi`, or for one
command with `filmot --api-provider direct COMMAND ...`. The `auto` policy is
cost-aware: the direct API charges credits from a monthly allowance (an
unscoped subtitle-search page costs 5 credits, or 1 when `--channel-id` names
one to five channels), while a legacy RapidAPI plan counts requests.

Under `auto`, a request that fails because the provider rejected its
credentials, ran out of quota, lacks the route, returned a server error or a
non-JSON response, or could not be reached is retried on the other provider.
Bad requests and response-contract violations are not retried, and a pinned
provider never fails over. Quota exhaustion is reported immediately rather
than retried; rate limiting still backs off before failing over.

After any command that used the Filmot API, a one-line summary is printed on
stderr:

```text
Filmot API: direct 1 request, 1 credit (7,980 credits left; resets 2026-11-19)
```

`--raw` results carry the same details under `_filmot.api`: requests and cache
hits per provider, credits charged and remaining, plan requests remaining, and
any failovers. The session ledger records them with the command's event.
`filmot config` shows which keys are configured and how routes resolve,
without printing credentials. Responses are cached separately per provider.

### Endpoint overrides and project configuration limits

`FILMOT_API_BASE_URL` and `RAPIDAPI_HOST` change where requests, and your
keys, are sent. You can set them only in the process environment or the
per-user `config.env`. For a provider whose key is set, the base URL must use
`https://` and the host must be a bare host name with an optional port.
Otherwise the command stops before sending any request, unless the other
provider is explicitly pinned; invalid configuration for an unused provider
does not block a pinned request.

When Filmot loads its configuration, it reads a project `.env` with these
limits:

- It cannot set the endpoint overrides.
- It cannot set the variables that locate the per-user config
  (`FILMOT_CONFIG_FILE`, `FILMOT_CONFIG_DIR`, `XDG_CONFIG_HOME`, `APPDATA`,
  `HOME`, `USERPROFILE`, and the Windows fallbacks `HOMEDRIVE`, `HOMEPATH`,
  `USERNAME`).
- It cannot set TLS trust or key logging (`REQUESTS_CA_BUNDLE`,
  `CURL_CA_BUNDLE`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `SSLKEYLOGFILE`).
- Its values are read literally, without `${VAR}` expansion.
- Names that are not plain identifiers are skipped.

Filmot locates the per-user config once, when it starts and before it reads
the project `.env`. Reloads and child processes reuse that location rather
than resolving it again. A parent process can point a child at a different
per-user config only by changing `FILMOT_CONFIG_FILE` or `FILMOT_CONFIG_DIR` to
a literal absolute path.

So the configuration Filmot loads, after a reload or in Filmot's own child
processes as well, sends your keys only to the endpoints your environment or
per-user config chose, over TLS verified the normal way. A protected value is
dropped when the project `.env` sets it and neither your environment nor your
per-user config does. If it would have changed anything (for an endpoint,
anything but the default), `filmot config` lists it, and Filmot warns once when
a command first sets up its Filmot API client.

A project `.env` cannot set `PYTHON*` variables either, because those steer
every Python child process. Filmot records the names a project `.env` did set,
and path settings such as `WEBSHARE_SESSION_FILE`, `FILMOT_RATE_LIMIT_DB`, or
`FILMOT_DATA_DIR` never expand them. So a project variable cannot complete a
trusted path like `$PROXY_DIR/sessions.txt`. The transcript helper process
starts without the current directory on its import path, so a module planted
there cannot replace one it uses.

These limits cover configuration loading, those paths, and the helper process.
A project `.env` still applies to everything else, including proxies and
Filmot's data and cache locations, so run Filmot only in directories you
trust.

When a provider asks Filmot to wait (`Retry-After`), a wait in whole seconds
is capped at 60 seconds. Any other value, such as an HTTP date, waits 5
seconds.

## Storage and configuration

Filmot deliberately separates research artifacts from machine-level runtime
state:

| Kind | Default location | Override |
| --- | --- | --- |
| Transcripts, research libraries, claim events, echo-analysis artifacts, watchlists, and session ledgers | `<current directory>/.filmot_data` | `FILMOT_DATA_DIR` |
| Per-user configuration and proxy credentials | Windows `%APPDATA%\filmot`; macOS `~/Library/Application Support/filmot`; Linux `${XDG_CONFIG_HOME:-~/.config}/filmot` | `FILMOT_CONFIG_DIR` |
| Per-user mutable state, including proxy health and rate limiting | Windows `%LOCALAPPDATA%\filmot`; macOS `~/Library/Application Support/filmot`; Linux `${XDG_STATE_HOME:-~/.local/state}/filmot` | `FILMOT_STATE_DIR` |
| Response cache | Native per-user cache directory (`%LOCALAPPDATA%`, `~/Library/Caches`, or `${XDG_CACHE_HOME:-~/.cache}`) under `filmot` | `FILMOT_CACHE_DIR` |

The response cache reads and deletes only files named like its entries (32
hexadecimal digits and `.json`), so other files in `FILMOT_CACHE_DIR` are left
alone. A numeric
setting such as `FILMOT_PROXY_RETRY_LIMIT` that doesn't hold a usable number
falls back to its default, with one warning, instead of stopping Filmot.

Configuration precedence is: existing process environment, per-user
`config.env`, then an explicit `.env` in the current working directory. The
per-user file can be redirected with `FILMOT_CONFIG_FILE`. Filmot does not
search parent directories for `.env`, so editable and packaged installs behave
the same way.

Activity logging accepts a shared investigation identity through
`filmot --session NAME COMMAND ...` or `FILMOT_SESSION`. Research, search,
transcript, download, channel-download, and claim mutations also accept a local
`--session` flag. The nearest explicit flag wins over a parent flag, then the
environment default. Without either, existing topic/date routing applies
(including a search's `--bulk-download TOPIC[:N]` destination). The investigation
groups activity events without altering queries, library destinations, or claim
topics. Nested saves and parallel channel-download items inherit the selection.

Proxy credential inventories are stored in the per-user configuration
directory with owner-only permissions where the platform supports them.
Credential-free proxy health, cooldown, rotation, and short-lived leases live
in `proxy/health.sqlite3` under the per-user state directory, allowing
concurrent Filmot processes to coordinate safely without writing secrets to
the database.

## Dependencies

| Package | Purpose |
|---------|---------|
| `requests` | HTTP API calls |
| `python-dotenv` | Environment variable management |
| `click` | CLI framework |
| `rich` | Terminal formatting, tables, colors |
| `youtube-transcript-api` | YouTube transcript download |
| `google-api-python-client` | YouTube Data API v3 channel resolution/upload enumeration |

**Optional dependencies:**

| Package | Purpose |
|---------|---------|
| `boto3` | AWS Transcribe fallback |
| `yt-dlp` | Audio download for AWS fallback |

The standard installation installs the required Python packages. The optional
`aws` extra installs `boto3` and `yt-dlp`; its system tools and configuration are
described below.

## AWS Transcribe fallback

`--fallback` downloads a video's audio, uploads it to an S3 bucket you
control, and transcribes it with AWS Transcribe when caption acquisition fails.
With the default transcript source, Filmot tries its subtitle index before
AWS. `--source filmot` cannot be combined with `--fallback`.

From the repository root, install the optional Python packages:

```bash
python -m pip install -e '.[aws]'
```

Install the **FFmpeg and ffprobe binaries** separately and ensure both are on
`PATH` in the shell that runs Filmot. Installing a Python package named
`ffmpeg` does not provide these executables. Filmot invokes yt-dlp with
`-x --audio-format mp3`; yt-dlp's
[post-processing options](https://github.com/yt-dlp/yt-dlp#post-processing-options)
require both binaries for audio extraction. Check their availability with:

```bash
ffmpeg -version
ffprobe -version
```

Configure AWS credentials with S3 and Transcribe access, then set these values
in your environment or per-user `config.env`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `FILMOT_S3_BUCKET` | _(none; required)_ | Bucket that receives the audio. |
| `FILMOT_AWS_PROFILE` | boto3's default credentials | AWS profile with S3 and Transcribe access. |
| `FILMOT_AWS_REGION` | `us-east-1` | Region for S3 and Transcribe. |

There is no default bucket, because S3 bucket names are global: a built-in name
would send other people's audio to whoever owns it. Without
`FILMOT_S3_BUCKET`, `--fallback` stops before downloading any audio.

See [Download transcripts](CLI_REFERENCE.md#download-transcripts) for command
examples and [Transcripts from Filmot's index](CLI_REFERENCE.md#transcripts-from-filmots-index)
for source-selection behavior.

## Webshare Proxy Pool (transcript fetching)

YouTube aggressively rate-limits and blocks data-center IPs (AWS, GCP, etc.).
When YouTube's IP block hits this CLI's transcript fetcher, the request returns
nothing usable. To avoid this, the CLI ships with a **dynamic Webshare proxy
pool**: it pulls a list of residential proxy sessions from your Webshare
account, rotates through them, tracks per-session health, and cools down or
retires sessions that get rate-limited / blocked / fail to connect.

### Setup

1. Sign up at [webshare.io](https://www.webshare.io/) and get a residential plan.
2. Copy your API token from the [user API keys page](https://dashboard.webshare.io/userapi/keys).
3. Add it to the per-user `config.env` (recommended) or project `.env`:
   ```bash
   WEBSHARE_API_TOKEN=your_40_char_token_here
   ```
4. Verify:
   ```bash
   filmot proxy refresh   # populate the pool
   filmot proxy status    # see available and recently healthy sessions
   filmot proxy test      # stream bounded probes through 3 sessions
   ```

### Configuration

| Env var                      | Default       | Purpose                                                            |
| ---------------------------- | ------------- | ------------------------------------------------------------------ |
| `WEBSHARE_API_TOKEN`         | _(unset)_     | Enables API-backed pool discovery and remote refresh.               |
| `WEBSHARE_SESSION_FILE`      | Per-user config `webshare_info.txt` | Optional file-backed `host:port:user:password` pool. |
| `FILMOT_PROXY_MODE`          | `proxy-only` if token set, else `auto` | `auto` \| `proxy-only` \| `primary-only` \| `direct-only` |
| `FILMOT_PROXY_COUNTRIES`     | _(all)_       | Comma-separated ISO-2 codes (e.g. `US,GB,CA`).                     |
| `FILMOT_PROXY_REFRESH_HOURS` | `6`           | How often the pool re-pulls the session list from Webshare.        |
| `FILMOT_PROXY_MAX_SESSIONS`  | `50`          | Cap on sessions kept in the pool.                                  |
| `FILMOT_PROXY_RETRY_LIMIT`   | `4`           | Max pool sessions to try per transcript before giving up.          |
| `FILMOT_PROXY_HEALTH_HOURS`  | `24`          | Recent-success window used by the “healthy” metric.                 |
| `FILMOT_PROXY_LEASE_SECONDS` | `120`         | Cross-process lease lifetime for a selected proxy session.          |
| `FILMOT_TRANSCRIPT_CONNECT_TIMEOUT` | `8`    | Per-request connection deadline in seconds.                        |
| `FILMOT_TRANSCRIPT_READ_TIMEOUT` | `15`       | Per-request read deadline in seconds.                              |
| `FILMOT_TRANSCRIPT_ROUTE_TIMEOUT` | `30`      | Overall deadline for one transcript route in seconds, Filmot index lookups included. |
| `FILMOT_TRANSCRIPT_SOURCE`   | `auto`        | `auto` \| `youtube` \| `filmot`; see [Transcripts from Filmot's index](CLI_REFERENCE.md#transcripts-from-filmots-index). |

On AWS hosts, `proxy-only` mode is the right default — direct requests will
typically be blocked. `proxy-only` tries only eligible pool sessions.
`direct-only` bypasses all proxy routes. `auto` tries the file/API pool first,
then the initialized primary route (an explicit proxy, legacy Webshare proxy,
environment proxy, or direct connection). `primary-only` uses only that
initialized primary route; the CLI selects it for an explicit `--proxy`.

### CLI commands

- `filmot proxy status` — separates sessions available now from sessions with a
  recent successful live fetch, plus untested, cooling, failing, retired, and
  invalid states. Use `--full` for every redacted session.
- `filmot proxy refresh` — re-pulls an API-backed list. For a file-backed pool,
  it re-reads the local session file while preserving retained health history.
  `--full` requests remote IP rotation only for API-backed pools.
- `filmot proxy test [-n N] [--video-id VID]` — probes up to `N` distinct
  loaded sessions by fetching a known short transcript through each. Run
  `proxy refresh` first if no sessions are loaded. It prints the redacted route
  before the call, streams each result, and enforces per-route and total
  command budgets.

### Storage and legacy migration

API-discovered credentials are cached in an owner-only inventory in Filmot's
per-user configuration directory. Proxy health and active leases are stored
transactionally in the credential-free per-user SQLite health database. This
keeps rotation coordinated across projects and prevents one process from
clobbering another process's counters or selecting the same leased session.

On first use, Filmot non-destructively migrates the former project-local proxy
files:

- `.filmot_data/webshare_info.txt` is copied to the per-user configuration
  directory.
- `.filmot_data/webshare_pool.json` and
  `.filmot_data/webshare_pool_file.json` contribute health counters to SQLite;
  API credentials are split into the private inventory.
- The legacy files are retained. An existing destination is never overwritten.

An explicitly configured custom `WEBSHARE_SESSION_FILE` remains authoritative.

### Legacy fallback

The older `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` static rotating
endpoint still works for users without an API token, but it doesn't get health
tracking or rotation — prefer the API-token path.
