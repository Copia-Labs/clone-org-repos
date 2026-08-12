#!/usr/bin/env python3
"""
Copia / Gitea Organization Repo Cloner
======================================

Clone every repository in a Copia (Gitea-based) organization, with either a
Tkinter GUI or a headless command-line interface. Designed to be packaged into
a single Windows .exe with PyInstaller.

Features
--------
* Settings for host/origin URL, auth token, organization and branch.
* Choosable target directory for the cloned repos.
* Scrollable live log that streams progress as it happens (GUI).
* Progress bar showing "<done> of <total> (<pct>%)".
* Parallel cloning with an adjustable worker count.
* History depth options: shallow (latest commit only) or full history.
* Config file (INI) persistence, with an *optional* "remember token" toggle.
* Full command-line interface so it can be scripted / scheduled.
* Per-repo error handling: one bad repo won't stop the whole run.
* Cancel button / Ctrl-C support.
* Optional log-to-file.

Dependencies:  gitpython, requests   (see requirements.txt)
Standard lib:  tkinter, configparser, argparse, threading, concurrent.futures

Author: generated for Copia
"""

import os
import sys
import stat
import time
import base64
import shutil
import queue
import getpass
import argparse
import threading
import configparser
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from git import Repo
    from git.exc import GitCommandError
    GIT_AVAILABLE = True
except Exception:  # pragma: no cover - only when gitpython missing
    GIT_AVAILABLE = False
    GitCommandError = Exception


APP_NAME = "Copia Org Repo Cloner"
DEFAULT_HOST = "https://app.copia.io"
DEFAULT_CONFIG_NAME = "clone_org_repos.ini"


# --------------------------------------------------------------------------- #
#  Filesystem helpers
# --------------------------------------------------------------------------- #
def _remove_readonly(func, path, _excinfo):
    """shutil.rmtree onerror handler: clear read-only bit (git objects) & retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def delete_folder(folder_path):
    """Delete a folder tree, coping with read-only git files on Windows."""
    if not os.path.exists(folder_path):
        return
    # onexc (3.12+) / onerror (older) compatibility.
    if sys.version_info >= (3, 12):
        shutil.rmtree(folder_path, onexc=lambda f, p, e: _remove_readonly(f, p, e))
    else:
        shutil.rmtree(folder_path, onerror=_remove_readonly)


# --------------------------------------------------------------------------- #
#  Token protection (encrypt-at-rest for the INI)
# --------------------------------------------------------------------------- #
#   Stored token formats:
#     enc:dpapi:<base64>  -> Windows DPAPI, tied to the current Windows user
#                            (cannot be read by another user or on another PC)
#     enc:obf:<base64>    -> portable XOR obfuscation fallback (NOT real
#                            security; only stops casual shoulder-surfing)
#     <anything else>     -> treated as plaintext (backward compatible)
_OBF_KEY = b"CopiaRepoCloner::do-not-store-real-secrets-in-plaintext"


def _dpapi_available():
    return sys.platform.startswith("win")


def _dpapi(data, encrypt):
    """Call Windows DPAPI CryptProtectData / CryptUnprotectData via ctypes."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if encrypt:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), ctypes.c_wchar_p("CopiaRepoCloner token"),
            None, None, None, 0, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _xor(data):
    return bytes(b ^ _OBF_KEY[i % len(_OBF_KEY)] for i, b in enumerate(data))


def protect_token(plaintext):
    """Encrypt (DPAPI on Windows) or obfuscate (XOR fallback) for storage."""
    if not plaintext:
        return ""
    raw = plaintext.encode("utf-8")
    if _dpapi_available():
        try:
            enc = _dpapi(raw, encrypt=True)
            return "enc:dpapi:" + base64.b64encode(enc).decode("ascii")
        except Exception:
            pass  # fall through to obfuscation
    return "enc:obf:" + base64.b64encode(_xor(raw)).decode("ascii")


def unprotect_token(stored):
    """Reverse protect_token. Returns plaintext, or '' if it can't be read."""
    if not stored:
        return ""
    try:
        if stored.startswith("enc:dpapi:"):
            enc = base64.b64decode(stored[len("enc:dpapi:"):])
            return _dpapi(enc, encrypt=False).decode("utf-8")
        if stored.startswith("enc:obf:"):
            enc = base64.b64decode(stored[len("enc:obf:"):])
            return _xor(enc).decode("utf-8")
    except Exception:
        return ""  # wrong user/machine, or corrupted value
    return stored  # legacy plaintext


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "Host": DEFAULT_HOST,
    "Token": "",
    "Organization": "",
    "Branch": "",           # blank => clone each repo's default branch
    "Target": "",           # where the repos go
    "Workers": "4",
    "History": "shallow",   # full | shallow
    "StopAfter": "0",       # 0 => no limit
    "RememberToken": "false",
    "LogToFile": "false",
}


def base_dir():
    """Directory the app lives in (works for both script and frozen .exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path():
    return os.path.join(base_dir(), DEFAULT_CONFIG_NAME)


def load_config(path=None):
    """Return a plain dict of settings, merging file values over DEFAULTS."""
    path = path or default_config_path()
    cfg = dict(DEFAULTS)
    parser = configparser.ConfigParser()
    # Preserve case for keys we don't know about; defaults are our source of truth.
    if os.path.exists(path):
        try:
            parser.read(path)
            if parser.has_section("DEFAULT") or parser.defaults():
                section = parser["DEFAULT"]
                for key in DEFAULTS:
                    if key in section:
                        cfg[key] = section.get(key)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not read config '{path}': {exc}", file=sys.stderr)
    # Decrypt/de-obfuscate the token that was stored (if any).
    stored_token = cfg.get("Token", "")
    cfg["Token"] = unprotect_token(stored_token)
    if stored_token.startswith("enc:") and not cfg["Token"]:
        print("Warning: the saved token could not be decrypted "
              "(different Windows user or machine?). Please re-enter it.",
              file=sys.stderr)
    return cfg


def save_config(cfg, path=None, include_token=False):
    """Persist settings to INI. Token only written when include_token is True."""
    path = path or default_config_path()
    parser = configparser.ConfigParser()
    out = dict(cfg)
    if include_token:
        out["Token"] = protect_token(cfg.get("Token", ""))
    else:
        out["Token"] = ""
    parser["DEFAULT"] = {k: str(v) for k, v in out.items()}
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)
    return path


def build_cli_args(settings, config_path):
    """Build the headless CLI argument list (no token) from settings.

    The token is intentionally NOT included; the scheduled run reads it
    (encrypted) from the INI referenced by --config.
    """
    args = ["--cli", "--config", os.path.abspath(config_path)]

    def add(flag, value):
        if value is not None and str(value).strip() != "":
            args.extend([flag, str(value).strip()])

    add("--host", settings.get("Host"))
    add("--org", settings.get("Organization"))
    add("--branch", settings.get("Branch"))
    add("--target", settings.get("Target"))
    add("--workers", settings.get("Workers"))
    add("--history", settings.get("History"))
    stop = str(settings.get("StopAfter", "0")).strip()
    if stop and stop != "0":
        args.extend(["--stop-after", stop])
    args.append("--log-to-file")
    return args


def _q(value):
    """Quote a single argument for a Windows command line."""
    s = str(value)
    if s.startswith("--"):
        return s  # flags never need quoting
    return '"' + s.replace('"', '\\"') + '"'


def write_scheduler_scripts(settings, config_path, app_runner, out_dir):
    """Write run_clone.bat and run_clone.ps1 into out_dir. Returns their paths."""
    args = build_cli_args(settings, config_path)
    quoted = " ".join(_q(a) for a in args)

    bat_path = os.path.join(out_dir, "run_clone.bat")
    ps1_path = os.path.join(out_dir, "run_clone.ps1")

    header_bat = (
        "@echo off\r\n"
        "REM Auto-generated by " + APP_NAME + ".\r\n"
        "REM Runs the cloner headlessly for Windows Task Scheduler.\r\n"
        "REM The auth token is read (encrypted, DPAPI) from the INI, so this\r\n"
        "REM task MUST run as the same Windows user that generated it.\r\n"
        'cd /d "%~dp0"\r\n'
    )
    with open(bat_path, "w", encoding="utf-8") as fh:
        fh.write(header_bat)
        fh.write(f"{app_runner['bat']} {quoted}\r\n")

    header_ps1 = (
        "# Auto-generated by " + APP_NAME + ".\r\n"
        "# Runs the cloner headlessly for Windows Task Scheduler.\r\n"
        "# The auth token is read (encrypted, DPAPI) from the INI, so this\r\n"
        "# task MUST run as the same Windows user that generated it.\r\n"
        "Set-Location -Path $PSScriptRoot\r\n"
    )
    with open(ps1_path, "w", encoding="utf-8") as fh:
        fh.write(header_ps1)
        fh.write(f"& {app_runner['ps1']} {quoted}\r\n")

    return bat_path, ps1_path


# --------------------------------------------------------------------------- #
#  Core engine  (no GUI / no argparse dependencies)
# --------------------------------------------------------------------------- #
class CloneEngine:
    """
    Does the actual cloning. Completely decoupled from any UI.

    Callbacks (all optional):
        log(message: str)                  -> stream a line of text
        progress(done: int, total: int)    -> update progress
        is_cancelled() -> bool             -> return True to abort early
    """

    def __init__(self, settings, log=None, progress=None, is_cancelled=None):
        self.s = settings
        self._log = log or (lambda m: print(m))
        self._progress = progress or (lambda d, t: None)
        self._is_cancelled = is_cancelled or (lambda: False)
        self._lock = threading.Lock()
        self._log_fh = None

    # -- small helpers ----------------------------------------------------- #
    def log(self, message):
        line = str(message)
        self._log(line)
        if self._log_fh:
            try:
                self._log_fh.write(line + "\n")
                self._log_fh.flush()
            except Exception:
                pass

    def cancelled(self):
        return bool(self._is_cancelled())

    def _host(self):
        return self.s.get("Host", DEFAULT_HOST).strip().rstrip("/") or DEFAULT_HOST

    def _headers(self):
        return {"Authorization": f"token {self.s['Token']}"}

    def _auth_url(self, clone_url):
        """Insert the token into the clone URL as basic-auth userinfo."""
        p = urlparse(clone_url)
        netloc = f"{self.s['Token']}@{p.hostname}"
        if p.port:
            netloc += f":{p.port}"
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))

    # -- API calls --------------------------------------------------------- #
    def list_orgs(self):
        """Return list of org usernames the token can see (raises on error)."""
        url = f"{self._host()}/api/v1/user/orgs"
        r = requests.get(url, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return [o["username"] for o in r.json()]

    def list_repos(self, org):
        """Return all repo objects for an org, following Gitea's paging."""
        repos = []
        page = 1
        while True:
            if self.cancelled():
                break
            r = requests.get(
                f"{self._host()}/api/v1/orgs/{org}/repos",
                params={"limit": 50, "page": page},
                headers=self._headers(),
                timeout=60,
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"Error retrieving repos ({r.status_code}): {r.text[:300]}"
                )
            batch = r.json()
            if not isinstance(batch, list):
                raise RuntimeError(f"Unexpected response (not a list): {batch}")
            if not batch:
                break
            repos.extend(batch)
            page += 1
        return repos

    # -- clone one repo ---------------------------------------------------- #
    def _clone_kwargs(self):
        kwargs = {}
        branch = self.s.get("Branch", "").strip()
        if branch:
            kwargs["branch"] = branch
        history = self.s.get("History", "shallow").strip().lower()
        if history == "shallow":
            kwargs["depth"] = 1
        # "full" => no depth kwarg (clone entire history)
        return kwargs

    # Environment for every git subprocess:
    #  * GIT_TERMINAL_PROMPT=0  -> never block forever on a credential prompt
    #  * low-speed limit/time   -> abort a stalled transfer instead of hanging
    _GIT_ENV = {
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_HTTP_LOW_SPEED_LIMIT": "1000",
        "GIT_HTTP_LOW_SPEED_TIME": "60",
    }

    def _scrub(self, text):
        """Remove the token from any text before it's logged."""
        t = str(text)
        tok = self.s.get("Token", "")
        return t.replace(tok, "***") if tok else t

    def _reason(self, exc):
        """Extract the most useful one-line reason from a git error."""
        msg = getattr(exc, "stderr", "") or str(exc)
        for line in str(msg).splitlines():
            line = line.strip().strip("'").strip()
            low = line.lower()
            if low.startswith("fatal:") or low.startswith("error:") \
                    or "denied" in low or "not found" in low:
                return self._scrub(line)
        return self._scrub(str(msg).strip().replace("\n", " ")[:200])

    def _attempt_clone(self, url, dest, kwargs):
        if os.path.exists(dest):
            delete_folder(dest)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        Repo.clone_from(url, dest, env=dict(self._GIT_ENV), **kwargs)

    def _clone_one(self, repo, target):
        name = repo.get("full_name", repo.get("name", "<unknown>"))
        if self.cancelled():
            return ("cancelled", name, "cancelled before start")
        dest = os.path.join(target, name)
        url = self._auth_url(repo["clone_url"])
        kwargs = self._clone_kwargs()
        try:
            self._attempt_clone(url, dest, kwargs)
            self.log(f"  [ok]   {name}")
            return ("ok", name, "")
        except GitCommandError as exc:
            reason = self._reason(exc)
            self.log(f"  [FAIL] {name}: {reason}")
            return ("fail", name, reason)
        except Exception as exc:  # noqa: BLE001
            reason = self._scrub(exc)
            self.log(f"  [FAIL] {name}: {reason}")
            return ("fail", name, reason)

    # -- main entry -------------------------------------------------------- #
    def run(self):
        """Execute the full clone. Returns a summary dict."""
        if not GIT_AVAILABLE:
            self.log("ERROR: GitPython is not installed. Run: pip install gitpython")
            return {"ok": 0, "fail": 0, "total": 0, "cancelled": True}

        if not self.s.get("Token"):
            self.log("ERROR: No auth token provided.")
            return {"ok": 0, "fail": 0, "total": 0, "cancelled": True}

        # Optional file log.
        if str(self.s.get("LogToFile", "")).lower() in ("1", "true", "yes"):
            try:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_path = os.path.join(base_dir(), f"clone_log_{stamp}.txt")
                self._log_fh = open(log_path, "w", encoding="utf-8")
                self.log(f"Writing log to: {log_path}")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Could not open log file: {exc}")

        org = self.s.get("Organization", "").strip()
        target = os.path.abspath(self.s.get("Target", "").strip() or os.getcwd())

        try:
            # Resolve org if not specified.
            if not org:
                self.log("No organization set - looking up your organizations...")
                orgs = self.list_orgs()
                if not orgs:
                    self.log("ERROR: No organizations found for this token.")
                    return {"ok": 0, "fail": 0, "total": 0, "cancelled": True}
                if len(orgs) == 1:
                    org = orgs[0]
                    self.log(f"Using the only organization available: {org}")
                else:
                    self.log("ERROR: Multiple organizations found; please set one: "
                             + ", ".join(orgs))
                    return {"ok": 0, "fail": 0, "total": 0, "cancelled": True,
                            "orgs": orgs}

            self.log(f"Host:   {self._host()}")
            self.log(f"Org:    {org}")
            self.log(f"Target: {target}")
            self.log("Fetching repository list...")

            os.makedirs(target, exist_ok=True)
            repos = self.list_repos(org)

            # StopAfter limit.
            try:
                stop_after = int(self.s.get("StopAfter", "0") or 0)
            except ValueError:
                stop_after = 0
            if stop_after > 0:
                repos = repos[:stop_after]
                self.log(f"StopAfter set: limiting to first {stop_after} repos.")

            total = len(repos)
            self.log(f"Found {total} repositories.")
            self._progress(0, total)
            if total == 0:
                return {"ok": 0, "fail": 0, "total": 0, "cancelled": False}

            try:
                workers = max(1, int(self.s.get("Workers", "4") or 4))
            except ValueError:
                workers = 4
            history = self.s.get("History", "shallow").strip().lower()
            self.log(f"Cloning with {workers} worker(s), history mode "
                     f"'{history}'...")

            done = 0
            ok = fail = cancelled = 0
            failures = []

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(self._clone_one, r, target): r for r in repos}
                for fut in as_completed(futures):
                    status, name, err = fut.result()
                    with self._lock:
                        done += 1
                        if status == "ok":
                            ok += 1
                        elif status == "cancelled":
                            cancelled += 1
                        else:
                            fail += 1
                            failures.append((name, err))
                        self._progress(done, total)
                    if self.cancelled():
                        # Stop scheduling further work; running clones finish.
                        for pending in futures:
                            pending.cancel()

            was_cancelled = self.cancelled()
            self.log("")
            self.log("=" * 48)
            self.log(f"Done. {ok} cloned, {fail} failed, {cancelled} skipped "
                     f"(of {total}).")
            if failures:
                self.log("Failed repos:")
                for n, _e in failures:
                    self.log(f"  - {n}")
            if was_cancelled:
                self.log("Run was cancelled before finishing.")
            return {"ok": ok, "fail": fail, "total": total,
                    "cancelled": was_cancelled, "failures": failures,
                    "org": org, "target": target}
        except requests.RequestException as exc:
            self.log(f"Network/API error: {exc}")
            return {"ok": 0, "fail": 0, "total": 0, "cancelled": True}
        except Exception as exc:  # noqa: BLE001
            self.log(f"Unexpected error: {exc}")
            return {"ok": 0, "fail": 0, "total": 0, "cancelled": True}
        finally:
            if self._log_fh:
                try:
                    self._log_fh.close()
                except Exception:
                    pass
                self._log_fh = None


# --------------------------------------------------------------------------- #
#  Command-line interface
# --------------------------------------------------------------------------- #
def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="clone_org_repos",
        description="Clone all repositories in a Copia/Gitea organization "
                    "(GUI by default, CLI with --cli).",
    )
    p.add_argument("--cli", action="store_true",
                   help="Run headless on the command line (no GUI).")
    p.add_argument("--gui", action="store_true",
                   help="Force the GUI even if other options are given.")
    p.add_argument("--config", metavar="PATH",
                   help=f"Path to an INI config file "
                        f"(default: {DEFAULT_CONFIG_NAME} next to the app).")
    p.add_argument("--host", help=f"Origin URL (default {DEFAULT_HOST}).")
    p.add_argument("--token", help="Copia auth token. If omitted in --cli mode "
                                   "you'll be prompted securely.")
    p.add_argument("--org", help="Organization name to clone.")
    p.add_argument("--branch", help="Branch to clone (blank = each repo's default).")
    p.add_argument("--target", help="Directory to clone the repos into.")
    p.add_argument("--workers", type=int, help="Number of parallel clones.")
    p.add_argument("--history", choices=["full", "shallow"],
                   help="History depth: full (entire history) or shallow "
                        "(latest commit only, default).")
    p.add_argument("--stop-after", type=int, metavar="N",
                   help="Clone only the first N repos (0 = all).")
    p.add_argument("--log-to-file", action="store_true",
                   help="Also write the run log to a timestamped text file.")
    p.add_argument("--list-orgs", action="store_true",
                   help="Just list the organizations your token can see, then exit.")
    p.add_argument("--save-config", action="store_true",
                   help="Save the resulting settings to the config file.")
    p.add_argument("--remember-token", action="store_true",
                   help="With --save-config, also persist the token (plaintext!).")
    return p


def settings_from_args(args):
    """Merge CLI args over the config file over DEFAULTS."""
    cfg = load_config(args.config)
    mapping = {
        "host": "Host", "token": "Token", "org": "Organization",
        "branch": "Branch", "target": "Target", "workers": "Workers",
        "history": "History", "stop_after": "StopAfter",
    }
    for arg_key, cfg_key in mapping.items():
        val = getattr(args, arg_key, None)
        if val is not None:
            cfg[cfg_key] = str(val)
    if args.log_to_file:
        cfg["LogToFile"] = "true"
    return cfg


def run_cli(args):
    cfg = settings_from_args(args)

    if not cfg.get("Token"):
        try:
            cfg["Token"] = getpass.getpass("Enter your Copia Auth Token: ")
        except (EOFError, KeyboardInterrupt):
            print("\nNo token provided; aborting.")
            return 2

    engine = CloneEngine(cfg, log=lambda m: print(m))

    if args.list_orgs:
        try:
            for name in engine.list_orgs():
                print(name)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Error listing orgs: {exc}", file=sys.stderr)
            return 1

    # Simple textual progress bar on a single line.
    state = {"last": ""}

    def progress(done, total):
        if not total:
            return
        pct = int(done * 100 / total)
        bar_len = 30
        filled = int(bar_len * done / total)
        bar = "#" * filled + "-" * (bar_len - filled)
        line = f"\r[{bar}] {done}/{total} ({pct}%)"
        sys.stdout.write(line)
        sys.stdout.flush()
        state["last"] = line
        if done == total:
            sys.stdout.write("\n")

    engine._progress = progress  # attach after construction

    result = engine.run()

    if args.save_config:
        path = save_config(cfg, args.config, include_token=args.remember_token)
        how = " (token encrypted)" if _dpapi_available() else " (token obfuscated)"
        print(f"Settings saved to {path}"
              + (how if args.remember_token else " (token not saved)."))

    if result.get("cancelled") and result.get("total", 0) == 0:
        return 1
    return 0 if result.get("fail", 0) == 0 else 3


# --------------------------------------------------------------------------- #
#  Tkinter GUI
# --------------------------------------------------------------------------- #
def run_gui(initial_cfg=None, config_path=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    cfg = initial_cfg or load_config(config_path)
    config_path = config_path or default_config_path()

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("820x680")
    root.minsize(700, 560)

    # Thread-safe pipe from worker -> UI.
    ui_queue = queue.Queue()
    cancel_event = threading.Event()
    worker = {"thread": None}

    # ---- tk variables ---------------------------------------------------- #
    v_host = tk.StringVar(value=cfg.get("Host", DEFAULT_HOST))
    v_token = tk.StringVar(value=cfg.get("Token", ""))
    v_org = tk.StringVar(value=cfg.get("Organization", ""))
    v_branch = tk.StringVar(value=cfg.get("Branch", ""))
    v_target = tk.StringVar(value=cfg.get("Target", ""))
    v_workers = tk.StringVar(value=cfg.get("Workers", "4"))
    v_history = tk.StringVar(value=cfg.get("History", "shallow"))
    v_stop = tk.StringVar(value=cfg.get("StopAfter", "0"))
    v_remember = tk.BooleanVar(value=str(cfg.get("RememberToken", "false")).lower()
                               in ("1", "true", "yes"))
    v_logfile = tk.BooleanVar(value=str(cfg.get("LogToFile", "false")).lower()
                              in ("1", "true", "yes"))
    v_showtoken = tk.BooleanVar(value=False)
    v_status = tk.StringVar(value="Ready.")

    # ---- layout ---------------------------------------------------------- #
    pad = {"padx": 6, "pady": 4}
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    row = 0
    ttk.Label(frm, text="Origin URL:").grid(row=row, column=0, sticky="w", **pad)
    ttk.Entry(frm, textvariable=v_host).grid(row=row, column=1, columnspan=3,
                                             sticky="ew", **pad)

    row += 1
    ttk.Label(frm, text="Auth token:").grid(row=row, column=0, sticky="w", **pad)
    token_entry = ttk.Entry(frm, textvariable=v_token, show="*")
    token_entry.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

    def toggle_token():
        token_entry.config(show="" if v_showtoken.get() else "*")

    ttk.Checkbutton(frm, text="Show", variable=v_showtoken,
                    command=toggle_token).grid(row=row, column=3, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="Organization:").grid(row=row, column=0, sticky="w", **pad)
    org_combo = ttk.Combobox(frm, textvariable=v_org)
    org_combo.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

    def fetch_orgs():
        try:
            eng = CloneEngine(collect_settings())
            names = eng.list_orgs()
            org_combo["values"] = names
            if names and not v_org.get():
                v_org.set(names[0])
            append_log(f"Found organizations: {', '.join(names) if names else '(none)'}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Could not fetch organizations:\n{exc}")

    ttk.Button(frm, text="Fetch", command=fetch_orgs).grid(
        row=row, column=3, sticky="ew", **pad)

    row += 1
    ttk.Label(frm, text="Branch:").grid(row=row, column=0, sticky="w", **pad)
    ttk.Entry(frm, textvariable=v_branch).grid(row=row, column=1, sticky="ew", **pad)
    ttk.Label(frm, text="(blank = each repo's default)").grid(
        row=row, column=2, columnspan=2, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="Target directory:").grid(row=row, column=0, sticky="w", **pad)
    ttk.Entry(frm, textvariable=v_target).grid(row=row, column=1, columnspan=2,
                                               sticky="ew", **pad)

    def browse_target():
        chosen = filedialog.askdirectory(title="Choose target directory")
        if chosen:
            v_target.set(chosen)

    ttk.Button(frm, text="Browse...", command=browse_target).grid(
        row=row, column=3, sticky="ew", **pad)

    # ---- options row ----------------------------------------------------- #
    row += 1
    opt = ttk.LabelFrame(frm, text="Options", padding=8)
    opt.grid(row=row, column=0, columnspan=4, sticky="ew", **pad)
    for c in range(6):
        opt.columnconfigure(c, weight=1)

    ttk.Label(opt, text="Workers:").grid(row=0, column=0, sticky="w", padx=4)
    ttk.Spinbox(opt, from_=1, to=32, width=5, textvariable=v_workers).grid(
        row=0, column=1, sticky="w", padx=4)

    ttk.Label(opt, text="History:").grid(row=0, column=2, sticky="e", padx=4)
    hist_combo = ttk.Combobox(opt, textvariable=v_history, width=12, state="readonly",
                              values=["shallow", "full"])
    hist_combo.grid(row=0, column=3, sticky="w", padx=4)
    ttk.Label(opt, text="(shallow = latest commit; full = all history)").grid(
        row=0, column=4, columnspan=2, sticky="w", padx=4)

    ttk.Label(opt, text="Stop after (0=all):").grid(row=1, column=0, sticky="w",
                                                    padx=4, pady=(6, 0))
    ttk.Spinbox(opt, from_=0, to=100000, width=7, textvariable=v_stop).grid(
        row=1, column=1, sticky="w", padx=4, pady=(6, 0))
    ttk.Checkbutton(opt, text="Remember token (encrypted)",
                    variable=v_remember).grid(row=1, column=2, columnspan=2,
                                              sticky="w", padx=4, pady=(6, 0))
    ttk.Checkbutton(opt, text="Write log file",
                    variable=v_logfile).grid(row=1, column=4, columnspan=2,
                                             sticky="w", padx=4, pady=(6, 0))

    # ---- action buttons -------------------------------------------------- #
    row += 1
    btns = ttk.Frame(frm)
    btns.grid(row=row, column=0, columnspan=4, sticky="ew", **pad)
    start_btn = ttk.Button(btns, text="Start Cloning")
    cancel_btn = ttk.Button(btns, text="Cancel", state="disabled")
    start_btn.pack(side="left", padx=4)
    cancel_btn.pack(side="left", padx=4)
    ttk.Button(btns, text="Save Settings",
               command=lambda: do_save()).pack(side="left", padx=4)
    ttk.Button(btns, text="Open Target",
               command=lambda: open_target()).pack(side="left", padx=4)
    ttk.Button(btns, text="Open Log Folder",
               command=lambda: open_log_folder()).pack(side="left", padx=4)
    ttk.Button(btns, text="Make Scheduler Script",
               command=lambda: make_scheduler_scripts()).pack(side="left", padx=4)
    ttk.Button(btns, text="Clear Log",
               command=lambda: clear_log()).pack(side="left", padx=4)

    # ---- progress -------------------------------------------------------- #
    row += 1
    prog = ttk.Progressbar(frm, mode="determinate", maximum=100)
    prog.grid(row=row, column=0, columnspan=4, sticky="ew", **pad)
    row += 1
    status_row = ttk.Frame(frm)
    status_row.grid(row=row, column=0, columnspan=4, sticky="ew", **pad)
    status_row.columnconfigure(1, weight=1)
    v_spin = tk.StringVar(value="")
    spin_lbl = ttk.Label(status_row, textvariable=v_spin, width=14,
                         font=("Consolas", 9))
    spin_lbl.grid(row=0, column=0, sticky="w")
    ttk.Label(status_row, textvariable=v_status).grid(row=0, column=1, sticky="w")

    # ---- log ------------------------------------------------------------- #
    row += 1
    frm.rowconfigure(row, weight=1)
    log_frame = ttk.Frame(frm)
    log_frame.grid(row=row, column=0, columnspan=4, sticky="nsew", **pad)
    log_frame.rowconfigure(0, weight=1)
    log_frame.columnconfigure(0, weight=1)
    log_text = tk.Text(log_frame, wrap="none", height=14, state="disabled",
                       background="#101418", foreground="#d6dde4",
                       insertbackground="#d6dde4")
    log_text.grid(row=0, column=0, sticky="nsew")
    yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll = ttk.Scrollbar(log_frame, orient="horizontal", command=log_text.xview)
    xscroll.grid(row=1, column=0, sticky="ew")
    log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

    # ---- helpers --------------------------------------------------------- #
    def append_log(message):
        log_text.config(state="normal")
        log_text.insert("end", message + "\n")
        log_text.see("end")
        log_text.config(state="disabled")

    def clear_log():
        log_text.config(state="normal")
        log_text.delete("1.0", "end")
        log_text.config(state="disabled")

    def collect_settings():
        return {
            "Host": v_host.get(), "Token": v_token.get(),
            "Organization": v_org.get(), "Branch": v_branch.get(),
            "Target": v_target.get(), "Workers": v_workers.get(),
            "History": v_history.get(),
            "StopAfter": v_stop.get(),
            "RememberToken": str(v_remember.get()),
            "LogToFile": str(v_logfile.get()),
        }

    def do_save():
        settings = collect_settings()
        path = save_config(settings, config_path, include_token=v_remember.get())
        how = " (token encrypted)" if _dpapi_available() else " (token obfuscated)"
        v_status.set(f"Settings saved to {path}"
                     + (how if v_remember.get() else " (token not saved)"))
        append_log(v_status.get())

    def open_in_file_manager(path):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            messagebox.showinfo(APP_NAME, f"Folder does not exist yet:\n{path}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: SIM115
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Could not open folder:\n{exc}")

    def open_target():
        open_in_file_manager(v_target.get().strip() or ".")

    def open_log_folder():
        # Log files are written next to the app (see CloneEngine.run()).
        open_in_file_manager(base_dir())

    def make_scheduler_scripts():
        settings = collect_settings()
        if not settings.get("Token", "").strip():
            messagebox.showwarning(
                APP_NAME, "Enter your auth token first — the scheduled task "
                          "needs it saved (encrypted) to run unattended.")
            return
        if not settings.get("Organization", "").strip():
            if not messagebox.askyesno(
                    APP_NAME, "No organization is set. The scheduled run will "
                              "fail without one. Create the scripts anyway?"):
                return
        try:
            # Save settings incl. the DPAPI-encrypted token so the unattended
            # run can authenticate without a plaintext secret in the script.
            save_config(settings, config_path, include_token=True)

            # Decide how the scripts should launch the app.
            if getattr(sys, "frozen", False):
                exe = _q(sys.executable)
                runner = {"bat": exe, "ps1": exe}
            else:
                exe_path = os.path.join(base_dir(), "CopiaRepoCloner.exe")
                if os.path.exists(exe_path):
                    exe = _q(exe_path)
                    runner = {"bat": exe, "ps1": exe}
                else:
                    script = _q(os.path.abspath(__file__))
                    runner = {"bat": f"python {script}",
                              "ps1": f"python {script}"}

            bat_path, ps1_path = write_scheduler_scripts(
                settings, config_path, runner, base_dir())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Could not create scripts:\n{exc}")
            return

        append_log(f"Created scheduler scripts:\n  {bat_path}\n  {ps1_path}")
        v_status.set("Scheduler scripts created.")
        messagebox.showinfo(
            APP_NAME,
            "Created two scripts next to the app:\n\n"
            f"  {os.path.basename(bat_path)}\n"
            f"  {os.path.basename(ps1_path)}\n\n"
            "Point Windows Task Scheduler at either one (Action = Start a "
            "program).\n\n"
            "IMPORTANT: the token is stored encrypted for YOUR Windows "
            "account, so schedule the task to run as the same user (and it "
            "does not need to run only when you're logged on, but it must be "
            "your account). Logs are written next to the app.")

    # ---- worker plumbing ------------------------------------------------- #
    def start_clone():
        if worker["thread"] and worker["thread"].is_alive():
            return
        if not v_token.get().strip():
            messagebox.showwarning(APP_NAME, "Please enter your auth token.")
            return
        if not v_target.get().strip():
            if not messagebox.askyesno(
                    APP_NAME,
                    "No target directory set. Clone into the current folder?"):
                return

        cancel_event.clear()
        prog["value"] = 0
        spin_state["start"] = None  # restart the elapsed clock
        v_status.set("Starting...")
        start_btn.config(state="disabled")
        cancel_btn.config(state="normal")

        settings = collect_settings()

        def log_cb(msg):
            ui_queue.put(("log", msg))

        def progress_cb(done, total):
            ui_queue.put(("progress", (done, total)))

        def worker_fn():
            engine = CloneEngine(
                settings,
                log=log_cb,
                progress=progress_cb,
                is_cancelled=cancel_event.is_set,
            )
            result = engine.run()
            ui_queue.put(("done", result))

        t = threading.Thread(target=worker_fn, daemon=True)
        worker["thread"] = t
        t.start()

    def cancel_clone():
        cancel_event.set()
        v_status.set("Cancelling... (in-flight clones will finish)")
        cancel_btn.config(state="disabled")

    start_btn.config(command=start_clone)
    cancel_btn.config(command=cancel_clone)

    # ---- poll the queue -------------------------------------------------- #
    def pump_queue():
        # Drain the queue each tick. This loop must NEVER die: the reschedule
        # is in a finally, and each event is handled in its own try/except, so
        # one bad event can't freeze all future UI updates.
        try:
            for _ in range(1000):  # bounded drain per tick
                try:
                    kind, payload = ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "log":
                        append_log(payload)
                    elif kind == "progress":
                        done, total = payload
                        pct = int(done * 100 / total) if total else 0
                        prog["value"] = pct
                        v_status.set(f"{done} of {total} ({pct}%)")
                    elif kind == "done":
                        start_btn.config(state="normal")
                        cancel_btn.config(state="disabled")
                        total = payload.get("total", 0)
                        if payload.get("cancelled") and total:
                            v_status.set("Cancelled.")
                        elif total == 0:
                            v_status.set("Nothing to do.")
                        else:
                            # Force the bar full so a dropped progress event
                            # can't leave it stuck below 100%.
                            prog["value"] = 100
                            v_status.set(
                                f"Complete: {payload.get('ok', 0)} cloned, "
                                f"{payload.get('fail', 0)} failed "
                                f"(of {total}).")
                except Exception as exc:  # noqa: BLE001
                    try:
                        append_log(f"[ui] display error (non-fatal): {exc}")
                    except Exception:
                        pass
        finally:
            root.after(120, pump_queue)

    # ---- liveness spinner ------------------------------------------------ #
    _SPIN_FRAMES = "|/-\\"
    spin_state = {"i": 0, "start": None}

    def animate_spinner():
        running = bool(worker["thread"] and worker["thread"].is_alive())
        if running:
            if spin_state["start"] is None:
                spin_state["start"] = time.monotonic()
            spin_state["i"] = (spin_state["i"] + 1) % len(_SPIN_FRAMES)
            secs = int(time.monotonic() - spin_state["start"])
            v_spin.set(f"{_SPIN_FRAMES[spin_state['i']]} working {secs // 60}:"
                       f"{secs % 60:02d}")
        else:
            spin_state["start"] = None
            v_spin.set("")
        root.after(150, animate_spinner)

    def on_close():
        if worker["thread"] and worker["thread"].is_alive():
            if not messagebox.askyesno(
                    APP_NAME, "A clone is still running. Quit anyway?"):
                return
            cancel_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    append_log(f"{APP_NAME} ready.")
    if not GIT_AVAILABLE:
        append_log("WARNING: GitPython not installed - cloning will fail. "
                   "Run: pip install gitpython")
    root.after(120, pump_queue)
    root.after(150, animate_spinner)
    root.mainloop()


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    # Decide GUI vs CLI. --gui always wins; --cli / --list-orgs force CLI.
    force_cli = args.cli or args.list_orgs
    if args.gui:
        run_gui(config_path=args.config)
        return 0
    if force_cli:
        return run_cli(args)

    # No mode flag: try the GUI, fall back to CLI if there's no display.
    try:
        run_gui(config_path=args.config)
        return 0
    except Exception as exc:  # noqa: BLE001  (e.g. no display available)
        print(f"GUI unavailable ({exc}); falling back to CLI. "
              f"Use --cli to silence this.", file=sys.stderr)
        return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
