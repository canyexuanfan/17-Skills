# rweb

A command-line client for reddit.com. No browser, no API key, no third-party
package — one module plus `curl`.

Works as a guest out of the box: listings, threads with their full comment trees,
search, user profiles and Atom feeds. Add your own account cookie to unlock
account-level features (saved / hidden / subscriptions / votes) and the write
operations.

## Install

```bash
git clone https://github.com/canyexuanfan/17-Skills.git
cd 17-Skills/skills/rweb

bash install.sh              # puts `rweb` in ~/.local/bin
# or, in a network sandbox:
pipx install .
# or, without installing anything:
python3 -m rweb --help
```

Requirements: **Python 3.8+** and **`curl` on `PATH`**. No third-party packages.

The session-handling core ships as a **prebuilt native extension** for five platforms —
there is nothing to compile at install time and no build toolchain required:

```
rweb/rcore/bin/linux-x86_64/_m0.abi3.so     # x86_64 Linux
rweb/rcore/bin/linux-aarch64/_m0.abi3.so    # ARM64 Linux
rweb/rcore/bin/windows-amd64/_m0.abi3.pyd   # Windows x64
rweb/rcore/bin/macos-arm64/_m0.abi3.so      # Apple silicon
rweb/rcore/bin/macos-x86_64/_m0.abi3.so     # Intel macOS
```

They are stable-ABI (`abi3`) builds, so they work on Python 3.9 and newer without
recompiling. On any other platform `rweb` will tell you which platforms are bundled.

## Self-check

```bash
rweb --selftest          # or: rweb selftest
```

Reports the running platform, the exact core module that got loaded, exercises the
classifier offline, and then performs a **live** session setup. Use it to confirm a
build works on a given machine — it is the fastest way to tell a platform problem
apart from a network problem. `rweb doctor` prints the same core-module line as its
first row.

## Quick start

```bash
rweb doctor                                # connectivity self-check (4 checks)
rweb caps                                  # what this build can do, one screen
rweb session                               # initialise a visitor session

rweb json /r/programming/hot --limit 100   # any listing
rweb json /r/programming/new --pages 3 --out posts.jsonl
rweb json /r/rust/comments/<id>            # a thread with its comment tree
rweb json "/search?q=kubernetes&sort=new"  # search
rweb json /user/spez/submitted             # a user's posts

rweb rss /r/programming                    # lightweight Atom feed
```

## Commands

| Command | What it does |
|:--|:--|
| `session` | initialise a visitor session (no account needed) |
| `login` | store your own account cookie |
| `doctor` | 4-point connectivity self-check + bundled core module |
| `selftest` | check the bundled core module and run a live session setup (also `rweb --selftest`) |
| `caps` | capability list for the current mode (guest / logged in) |
| `me` | your account (`/api/me.json`) |
| `json` | read any `.json` path — listings, threads, search, users |
| `rss` | read any `.rss` feed |
| `ops` | the operation table (`--read` / `--write`) |
| `op` | call one operation directly (`--var k=v`) |
| `api` | raw GET on a legacy / JSON path (`--param k=v`) |
| `vote` | vote on a post or comment (`--yes`) |
| `subscribe` | subscribe / unsubscribe a subreddit (`--yes`) |

Global flags: `--json` (machine output), `--dry-run` (build the request, don't send),
`--tor` / `--direct` (egress), `--jar` (cookie jar path).

## Output contract

`--json` prints **one JSON object per line and nothing else**, so it pipes cleanly:

```bash
rweb --json json /r/programming/hot --limit 100 | jq -r '.title'
rweb --json json /r/rust/comments/<id> | jq -r '.[1].data.children[].data.author'
```

Errors go to stderr with a non-zero exit code, so `set -e` pipelines behave.

## Using your own account

```bash
rweb login --cookie "token_v2=...; reddit_session=...; csrf_token=..."
rweb me
rweb json /subreddits/mine/subscriber
rweb json /user/<you>/saved
```

How to copy the cookie: open reddit.com in your browser, sign in, then
DevTools → Network → any reddit.com request → Request Headers → copy the whole
`Cookie:` line, and pass it to `rweb login --cookie "..."`.

The cookie is stored locally at `~/.config/rweb/cookies.jar` (mode 600) and is only
ever sent to reddit.com.

## Write operations

Every operation that changes state requires an explicit `--yes`:

```bash
rweb --dry-run op UpdatePostVoteState --var input.postId=t3_xxx --var input.voteState=UP
rweb op        UpdatePostVoteState --var input.postId=t3_xxx --var input.voteState=UP --yes
```

**Values are case- and spelling-exact — a wrong value comes back as `500`, not as a
validation error:**

| Field | Legal values |
|:--|:--|
| `voteState` | `UP` / `DOWN` / `NONE` (not `UPVOTE`) |
| `saveState` | `SAVED` / `NONE` |
| `hideState` | `HIDDEN` / `NONE` |
| `favoriteState` | `FAVORITED` / `NONE` |
| `followState` | `FOLLOWED` / `UNFOLLOWED` |
| subscribe (legacy) | `action=sub` / `unsub` |

## Troubleshooting

- **`doctor` scores below 4/4** → read the rows. A failing `json api` / `graphql`
  with a passing `session check` and `rss` is almost always the per-egress quota:
  this is rate limiting, **not** a broken install. Wait, or change egress.
- **`rweb session` prints `session usable (upstream throttled, HTTP 403)`** → the
  session is fine, the upstream quota for your egress is full.
- **A path returns `404` with an empty body** → that path is not registered.
  A `404`/`401` carrying structured JSON, or a redirect to a login page, means the
  path exists and needs credentials.
- **A write returns `500`** → check the value table above first; `--dry-run` shows
  the request that would be sent.
- **An empty result** → check that the post/subreddit still exists before concluding
  anything about the tool.

## Scope and limits

- Read: listings, threads and comment trees, search, user profiles, feeds, and the
  public operation set — no account required.
- Account: saved / hidden / subscribed / votes and other state, once you are logged in.
- Everything sent goes to reddit.com over HTTPS. Nothing is uploaded anywhere else.
- Visitor reads carry a per-egress quota. Please respect reddit's terms of service and
  keep request rates human — this is a client, not a scraper farm.

## License

Apache-2.0. See LICENSE.
