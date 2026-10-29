# Chrome extension

CareerOS Application Assistant is the optional Manifest V3 companion. It
captures supported job pages, pairs with an account, tracks application context,
and previews approved autofill actions. It never submits applications automatically.

See the [system overview](../README.md), [backend](../backend/README.md), and
[dashboard](../frontend/README.md). [RIGHTS.md](../RIGHTS.md) permits unmodified
local evaluation; editing instructions apply to authorized contributors.

## Stack and prerequisites

- Node.js **22**, npm, and Chrome supporting Manifest V3.
- React 19, TypeScript, and Vite; [package.json](package.json) specifies versions.
- Normal local backend on `http://localhost:8000` and dashboard on
  `http://localhost:3000`, with an invented signed-in account.

The extension calls `http://localhost:8000/api/v1` directly, configured in
[shared constants](src/shared/constants.ts), rather than through Next.js.
Pairing/dashboard links also use port `3000`. There is no extension
`.env.example` or runtime API-URL setting in the current setup.

## Build and install

From `extension/`:

```sh
npm ci
npm run build
```

The complete build type-checks, builds popup/service-worker modules, separately
builds the content-script bundle, copies it and the manifest into `dist`, and
removes temporary `dist-content` output.

1. Open Chrome's `chrome://extensions` page.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select `extension/dist`, not source or repo root.
4. Confirm **CareerOS Application Assistant** appears without load errors.
   Optionally pin its toolbar icon.

The [manifest](manifest.json) references the built popup, `service-worker.js`,
and `content.js`. Missing-file errors usually require a complete rebuild or
selecting the correct directory.

## Pair with the dashboard

1. Start the normal backend/dashboard following their guides.
2. Open `http://localhost:3000/extension/connect` and sign in if needed.
3. Generate a pairing code with the page's action; copy it before expiry.
4. Open the extension popup, paste the code, and select **Connect**.
5. Confirm the popup shows the account. Loading the extension alone does not
   create an authenticated session.

The popup messages the background worker, which exchanges the code with the
extension auth API. Extension tokens form a separate revocable session, not
the dashboard's refresh cookie.

Use **Disconnect** to clear local session state and request backend revocation.
If the backend was unreachable, verify/revoke the session through the dashboard's
connected-app settings after connectivity returns. Generate a fresh code when
pairing expires or recovery fails. Never publish codes or tokens.

The isolated portfolio harness uses different ports; these extension defaults
target normal development, not that harness.

## Source structure and message flow

```text
extension/
├── manifest.json             # Permissions, hosts, popup, worker entries
├── src/
│   ├── popup/                # React pairing/import/autofill screens
│   ├── background/           # Service worker, API client, auth, tab context
│   ├── content/              # Extraction, form scanning/filling, event detection
│   └── shared/               # Typed messages, validation, types, autofill policy
├── tests/
│   ├── unit/                 # Vitest suites
│   └── e2e/                  # Browser specifications; automation not wired
├── vite.config.ts            # Popup/service-worker module build
├── vite.content.config.ts    # Standalone content-script IIFE build
└── vitest.config.ts          # jsdom configuration
```

Popup/content scripts communicate with the worker through shared messages.
The worker handles API calls and sessions; content scripts inspect/act on the
page under the autofill policy. Keep validation and eligibility rules shared.

## Development and debugging

After authorized source changes:

1. Run `npm run build` from `extension/`.
2. Reload the extension on `chrome://extensions`.
3. Refresh the target page to receive the rebuilt content script.
4. Reopen the popup and verify session state; pair again if needed.

`npm run dev` watches only the main Vite build. It does **not** perform the
complete content-script/manifest-copy sequence. Use the full build/reload loop
as the reliable documented workflow.

| Surface | Inspection point |
| --- | --- |
| Popup | Inspect the open popup document with Chrome DevTools |
| Worker | Service-worker inspection link on the extension card |
| Content script | Target page's DevTools console and content-script sources |
| Load/build error | Extension-card errors and terminal build output |
| Backend request | Worker request errors/backend logs; never expose token headers |

Manifest V3 workers can stop when idle; do not assume in-memory state survives.

### Permissions, sites, and safe evaluation

The manifest declares `storage`, `activeTab`, `scripting`, and `tabs`, with
host/content-script matches for LinkedIn jobs, Indeed, Greenhouse, Lever, and
Workday jobs. Localhost API permission enables backend requests. See
[manifest.json](manifest.json) for exact patterns.

Additional ATS URL heuristics do not grant page access or prove site support.
The manifest does not declare a general localhost job-page content script, so
an arbitrary local fixture page will not automatically receive it. Manual
testing needs an explicitly approved fixture/access setup; these instructions
do not broaden permissions or certify every site's full workflow.

Use invented data and review previews. Password, file, hidden, and sensitive
or unmapped unsafe fields are excluded by policy. Fill requires user initiation;
generation, fill completion, or an observed page event does not authorize an
automatic submission.

## Tests and quality checks

From `extension/`, after `npm ci`:

| Check | Command |
| --- | --- |
| Unit tests | `npm test` |
| Focused example | `npm test -- tests/unit/autofill-policy.test.ts` |
| Test watcher | `npm run test:watch` |
| Lint | `npm run lint` |
| Types | `npx tsc --noEmit` |
| Complete build | `npm run build` |

[ESLint configuration](eslint.config.mjs) exists, and the previously inspected
CI run passed extension lint. Older workflow guidance describes the setup as
unavailable; that statement conflicts with current tracked configuration.
Consult [CI](../.github/workflows/quality.yml) and actual command results rather
than treating historical warnings as a new result.

Vitest runs in jsdom with mocked browser APIs, not an unpacked Chrome extension.
E2E specifications exist, but Playwright is not wired into a runnable script.
Separate manual QA must cover installation, pairing, capture, preview, approved
fill, and session recovery when relevant.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing manifest/content script | Full build, correct `dist` selection, extension reload |
| Pairing fails | Backend/dashboard health, signed-in account, ports, code expiry |
| Old popup behavior | Rebuild, reload, close/reopen popup |
| Job not detected | Exact manifest host/path matches, page refresh, content-script errors |
| Field skipped | Preview and policy; do not bypass exclusions |
| Session cannot recover | Fresh pairing code and connected-app session verification |

## Workflow and verification status

See [AGENTS.md](../AGENTS.md) and the
[development workflow](../docs/development-workflow.md) before authorized edits.
Use repository Issues with sanitized diagnostics for support. This guide was
checked statically; no fresh build, installation, pairing, third-party-page
interaction, or browser QA was performed for this update.
