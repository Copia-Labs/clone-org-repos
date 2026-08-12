# Copia Org Repo Cloner

Clone every repository in a Copia (Gitea-based) organization — with a friendly
**Tkinter GUI**, a full **command-line interface**, and one-command **EXE**
packaging.

This is a rewrite of the original `CloneOrgRepos.py` script.

---

## What's new vs. the original

| Area | Original | This version |
|------|----------|--------------|
| Interface | Terminal prompts (InquirerPy) | GUI **and** CLI |
| Settings | INI only | GUI fields + INI + CLI flags |
| Target dir | INI `MoveTo`, temp-folder shuffle | Direct target dir picker |
| Progress | `print` per repo | Live scrollable log **+** % progress bar |
| Speed | Sequential | Parallel (adjustable worker count) |
| History | Hard-coded `depth=1` | **shallow** (latest commit) or **full** (all history) |
| Branch | Hard-coded `main` fallback | Blank = each repo's default branch |
| Errors | One failure stops the run | Per-repo error handling + summary |
| Token in URL | Assumes `https://` | Robust URL parsing; token scrubbed from logs |
| Packaging | — | PyInstaller `.exe` (script + spec) |

---

## Install & run (from source)

```bash
pip install -r requirements.txt

# Launch the GUI (default)
python clone_org_repos.py

# Headless / scripted
python clone_org_repos.py --cli --org my-org --target C:\repos --workers 6
```

`tkinter` ships with the standard Windows/macOS Python installers. On Linux you
may need `sudo apt install python3-tk`.

---

## GUI

1. **Origin URL** – defaults to `https://app.copia.io`.
2. **Auth token** – paste your Copia personal token (Show toggles visibility).
3. **Organization** – type it, or click **Fetch** to list the orgs your token
   can see and pick from the dropdown.
4. **Branch** – leave blank to clone each repo's default branch.
5. **Target directory** – **Browse…** to pick where repos land
   (they're placed under `target/<org>/<repo>`).
6. **Options**
   - **Workers** – how many repos to clone at once.
   - **History** – `shallow` (latest commit only, fast) or `full` (all history).
   - **Stop after** – clone only the first N repos (0 = all); great for testing.
   - **Remember token** – persist the token to the INI (encrypted — off by default).
   - **Write log file** – also save a timestamped `.txt` log next to the app.
7. **Start Cloning** streams progress into the scrollable log and drives the
   progress bar. **Cancel** stops scheduling new clones (in-flight ones finish).
   **Save Settings**, **Open Target**, **Open Log Folder**, and **Clear Log** do
   what they say (Open Log Folder opens the folder where the app and its log
   files live).

---

## Command line

```
python clone_org_repos.py [options]

  --cli                 Run headless (no GUI)
  --gui                 Force the GUI
  --config PATH         INI file to read/write (default: next to the app)
  --host URL            Origin URL (default https://app.copia.io)
  --token TOKEN         Auth token (prompted securely if omitted in --cli)
  --org NAME            Organization to clone
  --branch NAME         Branch (blank = repo default)
  --target DIR          Where to clone
  --workers N           Parallel clones
  --history {full,shallow}
  --stop-after N        Clone only the first N repos (0 = all)
  --log-to-file         Also write a timestamped log file
  --list-orgs           List orgs your token can see, then exit
  --save-config         Save resulting settings to the config file
  --remember-token      With --save-config, also persist the token (plaintext!)
```

Precedence is **CLI flag → config file → built-in default**, so you can keep a
base `clone_org_repos.ini` and override per run.

Examples:

```bash
# See which orgs you belong to
python clone_org_repos.py --cli --token XXXX --list-orgs

# Full-history clone of one org into a folder, 8 at a time
python clone_org_repos.py --cli --org acme --history full --workers 8 --target D:\backup

# Shallow clone (latest commit only) and remember settings (token encrypted)
python clone_org_repos.py --cli --org acme --history shallow --save-config --remember-token
```

Exit codes: `0` success · `1` fatal/setup error · `3` finished but some repos failed.

---

## Build a Windows EXE

On a Windows machine with Python installed:

```bat
build_exe.bat
```

…or manually:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller CopiaRepoCloner.spec
```

The executable appears at `dist\CopiaRepoCloner.exe`. Drop
`clone_org_repos.ini` next to it if you want saved settings to travel with it.

> **Note:** `git` must be installed on the machine running the EXE — GitPython
> shells out to the system `git`. On a locked-down box, install
> [Git for Windows](https://git-scm.com/download/win).

---

## Security note

The token is treated as a secret: it's hidden in the GUI, prompted with
`getpass` on the CLI, scrubbed out of any error text, and **never** written to
the INI unless you explicitly opt in with "Remember token" / `--remember-token`.

When you do save it, it is **encrypted at rest**:

- **Windows:** encrypted with **DPAPI** (`CryptProtectData`) and stored as
  `Token = enc:dpapi:...`. The ciphertext is bound to your Windows user
  account, so the INI is useless if copied to another user or machine — if
  that happens, the app just warns and asks you to re-enter the token. No
  password or key file to manage, and no extra dependencies (it's called
  through `ctypes`).
- **Other OSes (fallback):** stored as `enc:obf:...` using lightweight XOR
  obfuscation. This is **not** real encryption — it only stops casual
  shoulder-surfing. Prefer not saving the token on shared machines.

A plaintext token typed straight into the INI still works and gets
re-encrypted the next time settings are saved.
