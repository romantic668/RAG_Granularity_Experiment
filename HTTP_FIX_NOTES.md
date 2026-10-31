# HTTP transport update

This update adds `http_transport: curl` to the existing config while preserving
its other settings. It uses the curl executable already available on Windows.
The model, evidence, prompts, schemas, metrics, sampling and aggregation rules
are unchanged by this update. The original urllib option remains available.

Each API request launches a curl process. Reported client latency includes this
process startup overhead, temporary request/response I/O, network and inference.
Use the same transport for all three variants and report this limitation when
discussing efficiency. These are client-observed timings, not server-only timings.

Credentials are passed to curl via stdin, not command-line arguments. TLS
certificate verification remains enabled. Temporary request/response files are
removed after each call. Selected API error details are redacted and recorded.

The config and source fingerprints change. Start a fresh smoke output folder
(`runs/smoke_curl`), then run and audit the pilot before freezing the final protocol.
Do not reuse an older output directory or previously frozen protocol with this code.

Validation: 22 tests with local HTTP fixtures, including real curl subprocesses,
plus installer/rollback and offline demo checks on Linux. Windows and live OpenAI
API inference must still be checked on the user's computer. No final scores exist yet.

Backups are placed in `runs/http_fix_backup_<timestamp>` by the installer.
