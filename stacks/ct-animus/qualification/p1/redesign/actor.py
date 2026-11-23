"""Synthetic browser actor for the isolated P1 redesign spike qualification.

Runs inside the harness network only (http://auth-server:9000 with Host auth.lan).
It drives the flow executor API the way the web UI does, never auto-follows,
and reports only structural facts: components seen, page counts, outcome
classes and booleans. It never prints passwords, TOTP codes, cookies, codes or
tokens. Usage: python -I actor.py SET [SET ...]; sets are listed in SCENARIOS.
"""
import base64
import hashlib
import hmac
import http.client
import http.cookiejar
import json
import secrets
import struct
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request

HOST = "auth.lan"
ORIGIN = "http://" + HOST
EXECUTOR = "/api/v3/flows/executor/"
NATIVE = "dev.animusstudio.alkion.dev:/oauth2redirect"
LOOPBACK = "http://127.0.0.1:43819/callback"
STATE = Path("/state/users.json")
USERS = json.loads(STATE.read_text()) if STATE.exists() else {}
INTERACTIVE = {"ak-stage-prompt", "ak-stage-identification", "ak-stage-password",
               "ak-stage-authenticator-validate", "ak-stage-consent", "ak-stage-user-login"}
# Another actor process may have used any step up to now+1 for the same user, and a
# TOTP step is single-use: start every user's counter there.
USED_STEPS = {name: int(time.time()) // 30 + 1 for name in USERS}
LABEL_TAGS = {
    "Start a new sign-in": "check-entry", "Check that this account used": "check-final",
    "Check that you signed in": "check-authz-entry", "Check that Alkion's request": "check-authz-final",
}


def hotp(key, step, digits=6):
    """RFC 4226/6238 (SHA-1) code for one time step."""
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 10 ** digits


def totp(name, wrong=False):
    """Code for the first unused past or current step (future steps are refused)."""
    key = bytes.fromhex(USERS[name]["totp_key"])
    while True:
        now = int(time.time()) // 30
        step = next((s for s in (now - 1, now) if s > USED_STEPS.get(name, 0)), None)
        if step is not None:
            break
        time.sleep(1)
    USED_STEPS[name] = step
    code = hotp(key, step)
    return str((code + 1) % 1000000 if wrong else code).zfill(6)


class Browser:
    """One cookie jar = one browser profile (Custom Tab)."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()

    def clear(self):
        self.jar.clear()

    def snapshot(self):
        return list(self.jar)

    def restore(self, cookies):
        self.jar.clear()
        for cookie in cookies:
            self.jar.set_cookie(cookie)

    def request(self, method, path, body=None, form=False):
        encoded = None if body is None else (urlencode(body).encode() if form else json.dumps(body).encode())
        canonical = Request(ORIGIN + path, data=encoded, method=method)
        self.jar.add_cookie_header(canonical)
        headers = dict(canonical.header_items())
        headers.update({"Host": HOST, "Accept": "application/json", "Accept-Encoding": "identity",
                        "Origin": ORIGIN, "Referer": ORIGIN + "/", "Connection": "close"})
        if encoded is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        csrf = next((c.value for c in self.jar if c.name == "authentik_csrf"), None)
        if csrf:
            headers["X-Authentik-CSRF"] = csrf
        conn = http.client.HTTPConnection("auth-server", 9000, timeout=30)
        try:
            conn.request(method, path, body=encoded, headers=headers)
            response = conn.getresponse()
            self.jar.extract_cookies(response, canonical)
            raw = response.read(4 * 1048576)
            ctype = (response.getheader("Content-Type") or "").split(";", 1)[0].strip()
            try:
                data = json.loads(raw) if ctype == "application/json" else None
            except ValueError:
                data = None
            return response.status, response.getheader("Location"), ctype, data, raw
        finally:
            conn.close()


def pkce():
    verifier = secrets.token_urlsafe(48)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_path(state, challenge, prompt=None, client_id="alkion-lan", redirect=NATIVE, method="S256",
                   omit_challenge=False, extra=None):
    query = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
             "scope": "openid profile offline_access", "state": state, "nonce": secrets.token_urlsafe(16)}
    if not omit_challenge:
        query.update(code_challenge=challenge, code_challenge_method=method)
    if prompt:
        query["prompt"] = prompt
    query.update(extra or {})
    return "/application/o/authorize/?" + urlencode(query, quote_via=quote)


def classify_html(raw):
    if b"Redirect URI Error" in raw:
        return "redirect_uri_error"
    if b"Request has been denied." in raw or b"Permission denied" in raw:
        return "access_denied_page"
    return "html_other"



class Run:
    """One Connect (or direct login) through the executor, with page accounting.

    A page is one interactive challenge the user must answer (User Login without
    remember-me never renders one and is not counted). `hook(run, when, tag)` is called
    "before" answering and "after" a page was accepted, before its redirect is followed.
    """

    def __init__(self, browser, credentials, hook=None, totp_wrong=False, max_pages=12):
        self.browser = browser
        self.credentials = list(credentials)
        self.hook = hook
        self.totp_wrong = totp_wrong
        self.max_pages = max_pages
        self.pages = []
        self.outcome = None
        self.callback = None
        self.ceremony_user = None
        self.ceremonies = 0
        self.executions = []
        self.deny_message = None

    def tag(self, slug, component, challenge):
        if component == "ak-stage-identification":
            return slug + ":" + ("identification+password" if challenge.get("password_fields") else "identification")
        return slug + ":" + component.removeprefix("ak-stage-")

    def answer(self, slug, component, challenge):
        body = {"component": component}
        if component == "ak-stage-prompt":
            for field in challenge.get("fields", []):
                if field.get("type") == "checkbox":
                    body[field["field_key"]] = True
            return body
        if component == "ak-stage-identification":
            if self.ceremony_user is None:
                self.ceremony_user = self.credentials.pop(0) if len(self.credentials) > 1 else self.credentials[0]
            body["uid_field"] = self.ceremony_user
            if challenge.get("password_fields"):
                body["password"] = USERS[self.ceremony_user]["password"]
            return body
        if component == "ak-stage-password":
            body["password"] = USERS[self.ceremony_user]["password"]
            return body
        if component == "ak-stage-authenticator-validate":
            user = self.ceremony_user or self.credentials[0]
            if not USERS[user]["totp_key"]:
                return None
            body["code"] = totp(user, wrong=self.totp_wrong)
            return body
        if component == "ak-stage-consent":
            body["token"] = challenge["token"]
            return body
        if component == "ak-stage-user-login":
            body["remember_me"] = False
            return body
        return None

    def location(self, target):
        if target.startswith("dev.animusstudio.alkion.dev:") or target.startswith(LOOPBACK):
            query = parse_qs(urlsplit(target).query)
            self.callback = {"code": query.get("code", [None])[0], "error": query.get("error", [None])[0],
                             "state": query.get("state", [None])[0]}
            self.outcome = "code" if self.callback["code"] else "oauth_error:" + str(self.callback["error"])
            return None
        parts = urlsplit(target)
        if parts.netloc not in ("", HOST):
            self.outcome = "external_redirect"
            return None
        if parts.path.startswith("/if/flow/"):
            slug = parts.path.removeprefix("/if/flow/").strip("/")
            self.executions.append(slug)
            if slug == "animus-lan-normal":
                self.ceremonies += 1
                self.ceremony_user = None
            return EXECUTOR + slug + "/?" + urlencode({"query": parts.query})
        if parts.path.startswith(EXECUTOR) or parts.path == "/application/o/authorize/":
            return parts.path + ("?" + parts.query if parts.query else "")
        self.outcome = "terminal_ui:" + parts.path
        return None

    def drive(self, path):
        hops = 0
        while path and hops < 200:
            hops += 1
            status, location, ctype, data, raw = self.browser.request("GET", path)
            if path.startswith("/application/o/authorize/"):
                if status in (301, 302, 303) and location:
                    path = self.location(location)
                    continue
                self.outcome = (classify_html(raw) + f":{status}") if ctype == "text/html" else f"authorize_status:{status}"
                return self
            path = self.executor(path, status, location, data)
        if path:
            self.outcome = "hop_limit"
        return self

    def executor(self, path, status, location, data):
        slug = urlsplit(path).path.removeprefix(EXECUTOR).strip("/")
        while True:
            if status in (301, 302, 303) and location:
                return self.location(location)
            if not isinstance(data, dict):
                self.outcome = f"executor_status:{status}"
                return None
            component = data.get("component")
            if component == "xak-flow-redirect":
                return self.location(data.get("to", ""))
            if component in ("ak-stage-access-denied", "ak-stage-flow-error"):
                self.outcome = component.removeprefix("ak-stage-")
                self.deny_message = data.get("error_message") == (
                    "This sign-in could not be completed. Go back to Alkion and tap Connect to start again.")
                return None
            if component not in INTERACTIVE:
                self.outcome = "unexpected:" + str(component)
                return None
            tag = self.tag(slug, component, data)
            if component != "ak-stage-user-login":
                self.pages.append(tag)
            if len(self.pages) > self.max_pages:
                self.outcome = "page_limit"
                return None
            if self.hook:
                self.hook(self, "before", tag)
            body = self.answer(slug, component, data)
            if body is None:
                self.outcome = "stopped:" + tag
                return None
            status, location, _, data, _ = self.browser.request("POST", path, body)
            if status == 200 and isinstance(data, dict) and data.get("component") == component:
                self.outcome = "rejected:" + tag
                return None
            if self.hook:
                self.hook(self, "after", tag)


def connect(browser, users, prompt=None, path=None, **kwargs):
    state = secrets.token_urlsafe(16)
    verifier, challenge = pkce()
    keys = ("client_id", "redirect", "method", "omit_challenge", "extra")
    extra = {k: kwargs.pop(k) for k in keys if k in kwargs}
    run = Run(browser, users, **kwargs)
    run.path = path or authorize_path(state, challenge, prompt=prompt, **extra)
    run.drive(run.path)
    run.verifier = verifier
    run.state = state
    run.redirect = extra.get("redirect", NATIVE)
    return run


def direct_login(browser, users, flow="animus-lan-normal", query="", **kwargs):
    run = Run(browser, users, **kwargs)
    run.drive(EXECUTOR + flow + "/?" + urlencode({"query": query}))
    return run


def session_user(browser):
    """Username of the browser's authenticated Authentik session, or None."""
    status, _, _, data, _ = browser.request("GET", "/api/v3/core/users/me/")
    if status == 200 and isinstance(data, dict):
        return (data.get("user") or {}).get("username")
    return None


def redeem(browser, run, expected=None):
    """Token exchange; report only booleans about the signed ID token."""
    import jwt  # provided by the Authentik image
    if run.outcome != "code" or run.callback.get("state") != run.state:
        return {"redeemed": False}
    status, _, _, data, _ = browser.request("POST", "/application/o/token/", {
        "grant_type": "authorization_code", "code": run.callback["code"], "redirect_uri": run.redirect,
        "client_id": "alkion-lan", "code_verifier": run.verifier}, form=True)
    if status != 200 or not isinstance(data, dict) or "id_token" not in data:
        return {"redeemed": False, "token_status": status}
    _, _, _, jwks, _ = browser.request("GET", "/application/o/alkion-lan/jwks/")
    header = jwt.get_unverified_header(data["id_token"])
    key = next(k for k in jwks["keys"] if k.get("kid") == header.get("kid"))
    claims = jwt.decode(data["id_token"], jwt.PyJWK(key).key, algorithms=["RS256"], audience="alkion-lan",
                        issuer=ORIGIN + "/application/o/alkion-lan/")
    factor = claims.get("hub_factor_time")
    return {"redeemed": True, "signature_verified": True,
            "hub_policy": claims.get("hub_authentication_policy") == "animus-password-totp-mechanism-v2",
            "factor_time_int": type(factor) is int,
            "factor_before_auth_time": type(factor) is int and factor <= claims.get("auth_time", 0),
            "refresh_token": "refresh_token" in data,
            "subject_is_connecting_user": expected is not None and claims.get("preferred_username") == expected}


def summary(run, redeem_result=None, browser=None):
    result = {"outcome": run.outcome, "pages": len(run.pages), "sequence": run.pages,
              "normal_ceremonies": run.ceremonies, "executions": run.executions}
    if run.deny_message is not None:
        result["deny_notice_text"] = run.deny_message
    if redeem_result is not None:
        result["token"] = redeem_result
    if browser is not None:
        result["session_user"] = session_user(browser)
    return result


def ok_connect(browser, users, prompt="login", **kwargs):
    run = connect(browser, users, prompt=prompt, **kwargs)
    result = summary(run, redeem(browser, run, users[-1]), browser)
    result["_path"] = run.path
    return result


def strip(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


def denied(browser, users, prompt="login", **kwargs):
    return summary(connect(browser, users, prompt=prompt, **kwargs), browser=browser)


def wait_for_stale(seconds=305):
    time.sleep(seconds)


def scenario_deployed():
    """Deployed blueprint (c693d82) before the forward apply: reference page counts."""
    b = Browser()
    out = {"deployed_no_session_first": strip(ok_connect(b, ["t4-owner"]))}
    b.clear()
    out["deployed_no_session_again"] = strip(ok_connect(b, ["t4-owner"]))
    return out


def scenario_main():
    out = {}
    b = Browser()
    out["no_session_first_connect"] = strip(ok_connect(b, ["t4-owner"]))
    b.clear()
    out["no_session_again"] = strip(ok_connect(b, ["t4-owner"]))
    out["same_user_live_session"] = strip(ok_connect(b, ["t4-owner"]))
    out["same_user_live_session_again"] = strip(ok_connect(b, ["t4-owner"]))
    b.clear()
    out["other_user_first"] = strip(ok_connect(b, ["t4-other"]))
    out["other_user_live_then_owner"] = strip(ok_connect(b, ["t4-owner"]))
    b.clear()
    out["no_prompt_no_session"] = strip(ok_connect(b, ["t4-owner"], prompt=None))
    # Silent authorization: a fresh (<300 s) qualified session, a NEW request.
    out["silent_new_request_no_prompt"] = denied(b, ["t4-owner"], prompt=None, max_pages=0)
    out["new_request_prompt_login_needs_ceremony"] = denied(b, ["t4-owner"], max_pages=0)
    # Exact replay of an already fulfilled request (same PKCE challenge) within 300 s.
    b.clear()
    first = ok_connect(b, ["t4-owner"])
    out["replay_source"] = strip(first)
    out["replay_same_request_prompt_login"] = summary(connect(b, ["t4-owner"], path=first["_path"], max_pages=0), browser=b)
    b.clear()
    first = ok_connect(b, ["t4-owner"], prompt=None)
    out["replay_source_no_prompt"] = strip(first)
    out["replay_same_request_no_prompt"] = summary(connect(b, ["t4-owner"], path=first["_path"], max_pages=0), browser=b)
    # prompt=none, prompt=consent and executor-only response modes.
    b.clear()
    out["prompt_none_no_session"] = denied(b, ["t4-owner"], prompt="none", max_pages=0)
    ok_connect(b, ["t4-owner"])
    for label, kwargs in {
        "prompt_none_live": {"prompt": "none"},
        "prompt_consent_live": {"prompt": "consent"},
        "prompt_login_consent_live": {"prompt": "login consent"},
        "prompt_none_login_live": {"prompt": "none login"},
        "response_mode_form_post_live": {"extra": {"response_mode": "form_post"}},
        "response_mode_fragment_live": {"extra": {"response_mode": "fragment"}},
    }.items():
        kwargs.setdefault("prompt", "login")
        out[label] = denied(b, ["t4-owner"], max_pages=0, **kwargs)
    return out


def scenario_negatives():
    out = {}
    b = Browser()
    for name in ("t4-notp", "t4-nogroup", "t4-inactive", "t4-admin"):
        b.clear()
        out[name] = denied(b, [name])
    b.clear()
    out["wrong_totp"] = denied(b, ["t4-owner"], totp_wrong=True)
    for label, kwargs in {
        "other_client": {"client_id": "other-client"},
        "unregistered_redirect": {"redirect": "dev.animusstudio.alkion.dev:/oauth2redirect/"},
        "near_miss_loopback": {"redirect": "http://localhost:43819/callback"},
        "plain_pkce": {"method": "plain"},
        "missing_pkce": {"omit_challenge": True},
    }.items():
        for prompt in ("login", None):
            b.clear()
            ok_connect(b, ["t4-owner"])  # live qualified session: only the request is wrong
            out[f"{label}_prompt_{prompt}"] = denied(b, ["t4-owner"], prompt=prompt, max_pages=4, **kwargs)
    b.clear()
    out["loopback_callback"] = strip(ok_connect(b, ["t4-owner"], redirect=LOOPBACK))
    # Another flow's login event.
    b.clear()
    out["default_flow_login"] = summary(direct_login(b, ["t4-owner"], flow="default-authentication-flow"), browser=b)
    out["after_other_flow_event_no_prompt"] = denied(b, ["t4-owner"], prompt=None, max_pages=0)
    out["after_other_flow_event_prompt_login"] = strip(ok_connect(b, ["t4-owner"]))
    # Direct normal-flow login (no authorize request): marker has no request label.
    b.clear()
    out["direct_normal_login"] = summary(direct_login(b, ["t4-owner"]), browser=b)
    out["after_direct_normal_login_no_prompt"] = denied(b, ["t4-owner"], prompt=None, max_pages=0)
    # Ineligible live sessions with prompt=login: re-authentication, then denial.
    for name in ("t4-nogroup", "t4-admin"):
        b.clear()
        out[name + "_session_then_prompt_login"] = {
            "direct": summary(direct_login(b, [name], flow="default-authentication-flow"), browser=b),
            "connect": denied(b, [name])}
    # Replay: park the cookies after TOTP was accepted (before the login gate), finish,
    # then replay the parked cookies.
    b.clear()
    parked = {}

    def park(run, when, tag):
        if when == "after" and tag.endswith("authenticator-validate") and "cookies" not in parked:
            parked["cookies"] = b.snapshot()
    first = connect(b, ["p1r-replay"], hook=park)
    out["replay_parked_source"] = summary(first, redeem(b, first, "p1r-replay"), b)
    b.restore(parked["cookies"])
    out["replay_parked_session"] = summary(connect(b, ["p1r-replay"], max_pages=0), browser=b)
    return out


def scenario_forged():
    """Restored plan contexts from forged FlowTokens (created by `seed forge:*`)."""
    out = {}
    for variant in ("login", "full"):
        b = Browser()
        key = Path("/state/forged-" + variant).read_text().strip()
        run = direct_login(b, ["p1r-forge"], query=urlencode({"flow_token": key}))
        out["restored_plan_" + variant] = summary(run, browser=b)
        out["restored_plan_" + variant + "_then_connect_no_prompt"] = denied(b, ["p1r-forge"], prompt=None, max_pages=0)
    return out


def pause_on(tag_suffix, when):
    def hook(run, now, tag):
        if now == when and tag.endswith(tag_suffix) and not getattr(run, "paused", False):
            run.paused = True
            time.sleep(305)
    return hook


def scenario_slow_password():
    """Password accepted, then more than 300 s before the TOTP answer."""
    b = Browser()
    return {"pause_after_password": denied(b, ["t4-slow-a"], hook=pause_on("authenticator-validate", "before"))}


def scenario_slow_after_totp():
    """Both factors accepted, then more than 300 s before the login gate runs."""
    b = Browser()
    return {"pause_after_totp": denied(b, ["p1r-slow-d"], hook=pause_on("authenticator-validate", "after"))}


def scenario_slow_on_page():
    """More than 300 s on the username+password page before submitting it."""
    b = Browser()
    return {"pause_on_password_page": denied(b, ["p1r-slow-e"], hook=pause_on("identification+password", "before"))}


def scenario_slow_stale():
    b = Browser()
    out = {"login": strip(ok_connect(b, ["t4-slow-b"]))}
    wait_for_stale()
    out["stale_no_prompt"] = denied(b, ["t4-slow-b"], prompt=None, max_pages=0)
    out["stale_prompt_login"] = strip(ok_connect(b, ["t4-slow-b"]))
    return out


def scenario_slow_other_stale():
    b = Browser()
    out = {"other_login": strip(ok_connect(b, ["t4-slow-c-other"]))}
    wait_for_stale()
    out["owner_prompt_login_over_stale_other"] = strip(ok_connect(b, ["t4-slow-c"]))
    return out


def fault(users_ok, users_bad=()):
    out = {}
    b = Browser()
    for name in users_ok:
        b.clear()
        out["eligible:" + name] = strip(ok_connect(b, [name]))
    for name in users_bad:
        b.clear()
        out["ineligible:" + name] = denied(b, [name])
    return out


def scenario_restored():
    """After every fault was reverted by re-applying the candidate."""
    b = Browser()
    return {"owner": strip(ok_connect(b, ["t4-owner"]))}


def scenario_rollback():
    b = Browser()
    return {"rollback_no_session": strip(ok_connect(b, ["t4-owner"], max_pages=12))}


def scenario_reforward():
    b = Browser()
    return {"reforward_no_session": strip(ok_connect(b, ["t4-owner"]))}


SCENARIOS = {
    "deployed": scenario_deployed, "main": scenario_main, "negatives": scenario_negatives,
    "forged": scenario_forged, "slow-password": scenario_slow_password,
    "slow-after-totp": scenario_slow_after_totp, "slow-on-page": scenario_slow_on_page,
    "slow-stale": scenario_slow_stale, "slow-other-stale": scenario_slow_other_stale,
    "fault-inject-entry-gate": lambda: fault(["p1r-inj-a"]),
    "fault-inject-login-gate": lambda: fault(["p1r-inj-b"]),
    "fault-inject-denied-notice": lambda: fault(["p1r-inj-c"], ["p1r-inj-nogroup"]),
    "fault-inject-application": lambda: fault(["p1r-inj-a"]),
    "fault-inject-pkce": lambda: fault(["p1r-inj-b"]),
    "fault-negate-login-gate": lambda: fault(["p1r-inj-c"], ["p1r-inj-nogroup"]),
    "fault-negate-entry-gate": lambda: fault(["p1r-inj-a"]),
    "restored": scenario_restored, "rollback": scenario_rollback, "reforward": scenario_reforward,
}

if __name__ == "__main__":
    results = {}
    for name in sys.argv[1:]:
        try:
            results[name] = SCENARIOS[name]()
        except Exception as exc:  # report the class only; never request data
            results[name] = {"harness_error": type(exc).__name__}
    print("T4ACTOR " + json.dumps(results, sort_keys=True))
