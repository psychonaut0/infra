"""Synthetic browser actor for the isolated flow-simplification qualification.

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
                   omit_challenge=False):
    query = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
             "scope": "openid profile offline_access", "state": state}
    if not omit_challenge:
        query.update(code_challenge=challenge, code_challenge_method=method)
    if prompt:
        query["prompt"] = prompt
    return "/application/o/authorize/?" + urlencode(query, quote_via=quote)


def classify_html(raw):
    if b"Redirect URI Error" in raw:
        return "redirect_uri_error"
    if b"Request has been denied." in raw or b"Permission denied" in raw:
        return "access_denied_page"
    return "html_other"


class Run:
    """One Connect (or direct login) through the executor, with page accounting.

    A page is one interactive challenge the user must answer. Prompt pages are
    tagged by position inside each flow execution (entry, final), which reads the
    same for the previous and the new blueprint; label_tags cross-checks the new
    plain-word labels against that position.
    """

    POSITIONS = {"animus-lan-normal": ("check-entry", "check-final"),
                 "animus-lan-authorization": ("check-authz-entry", "check-authz-final")}

    def __init__(self, browser, credentials, prompt_hook=None, totp_wrong=False, max_pages=40):
        self.browser = browser
        self.credentials = list(credentials)  # usernames for successive ceremonies
        self.prompt_hook = prompt_hook
        self.totp_wrong = totp_wrong
        self.max_pages = max_pages
        self.pages = []
        self.errors = []
        self.label_tags = []
        self.outcome = None
        self.callback = None
        self.ceremony_user = None
        self.ceremonies = 0
        self.executions = []
        self.prompts_in_execution = 0

    def tag(self, slug, component, challenge):
        if component == "ak-stage-prompt":
            names = self.POSITIONS.get(slug, ())
            position = names[self.prompts_in_execution] if self.prompts_in_execution < len(names) else "prompt"
            self.prompts_in_execution += 1
            labels = [f.get("label", "") for f in challenge.get("fields", [])]
            by_label = next((t for prefix, t in LABEL_TAGS.items() for label in labels if label.startswith(prefix)), None)
            if by_label is not None:
                self.label_tags.append(by_label == position)
            return slug + ":" + position
        if component == "ak-stage-identification":
            return slug + ":" + ("identification+password" if challenge.get("password_fields") else "identification")
        return slug + ":" + component.removeprefix("ak-stage-")

    def answer(self, slug, component, challenge):
        body = {"component": component}
        if component == "ak-stage-prompt":
            if self.prompt_hook:
                self.prompt_hook(self, slug, challenge)
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
            if not USERS[self.ceremony_user]["totp_key"]:
                return None
            body["code"] = totp(self.ceremony_user, wrong=self.totp_wrong)
            return body
        if component == "ak-stage-consent":
            body["token"] = challenge["token"]
            return body
        if component == "ak-stage-user-login":
            body["remember_me"] = False
            return body
        return None

    def location(self, target):
        """Classify a redirect target; return the next request path or None when terminal."""
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
            self.prompts_in_execution = 0
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
            body = self.answer(slug, component, data)
            if body is None:
                self.outcome = "stopped:" + tag
                return None
            status, location, _, data, _ = self.browser.request("POST", path, body)
            # A valid response always redirects (stage_ok); the same challenge again is a
            # rejection, with or without listed errors.
            if status == 200 and isinstance(data, dict) and data.get("component") == component:
                self.errors.append(tag)
                self.outcome = "rejected:" + tag
                return None


def connect(browser, users, prompt=None, **kwargs):
    state = secrets.token_urlsafe(16)
    verifier, challenge = pkce()
    extra = {k: kwargs.pop(k) for k in ("client_id", "redirect", "method", "omit_challenge") if k in kwargs}
    run = Run(browser, users, **kwargs)
    run.drive(authorize_path(state, challenge, prompt=prompt, **extra))
    run.verifier = verifier
    run.state = state
    run.redirect = extra.get("redirect", NATIVE)
    return run


def direct_login(browser, users, flow="animus-lan-normal", **kwargs):
    run = Run(browser, users, **kwargs)
    run.drive(EXECUTOR + flow + "/?" + urlencode({"query": ""}))
    return run


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


def summary(run, redeem_result=None):
    result = {"outcome": run.outcome, "pages": len(run.pages), "sequence": run.pages,
              "normal_ceremonies": run.ceremonies, "executions": run.executions}
    if run.label_tags:
        result["labels_match_position"] = all(run.label_tags)
    if redeem_result is not None:
        result["token"] = redeem_result
    return result


def ok_connect(browser, users, prompt=None, **kwargs):
    run = connect(browser, users, prompt=prompt, **kwargs)
    return summary(run, redeem(browser, run, users[-1]))


def wait_for_stale(seconds=305):
    time.sleep(seconds)


def scenario_baseline():
    """Base (previous) blueprint: page counts for comparison."""
    out = {}
    b = Browser()
    out["base_no_session_first"] = ok_connect(b, ["t4-owner"])
    b.clear()
    out["base_no_session_again"] = ok_connect(b, ["t4-owner"])
    b.clear()
    out["base_no_session_prompt_login"] = ok_connect(b, ["t4-owner"], prompt="login")
    return out


def scenario_main():
    out = {}
    b = Browser()
    out["no_session_first_prompt_login"] = ok_connect(b, ["t4-owner"], prompt="login")
    b.clear()
    out["no_session_again_prompt_login"] = ok_connect(b, ["t4-owner"], prompt="login")
    out["same_user_live_prompt_login"] = ok_connect(b, ["t4-owner"], prompt="login")
    out["same_user_live_prompt_login_again"] = ok_connect(b, ["t4-owner"], prompt="login")
    b.clear()
    out["no_session_no_prompt"] = ok_connect(b, ["t4-owner"])
    out["same_user_live_no_prompt"] = ok_connect(b, ["t4-owner"])
    # Another user's live session in the same browser profile, then the owner.
    b.clear()
    out["other_user_first"] = ok_connect(b, ["t4-other"], prompt="login")
    out["other_user_live_then_owner_prompt_login"] = ok_connect(b, ["t4-owner"], prompt="login")
    out["owner_retry_after_other"] = ok_connect(b, ["t4-owner"], prompt="login")
    # Silent authorization: remembered consent and a fresh (<300 s) qualified session.
    b.clear()
    ok_connect(b, ["t4-owner"])
    silent = connect(b, ["t4-owner"], max_pages=0)
    out["silent_within_300s_remembered_consent"] = {"outcome": silent.outcome, "first_page": silent.pages[:1]}
    # prompt=none with no session and with a fresh qualified session.
    b.clear()
    run = connect(b, ["t4-owner"], prompt="none", max_pages=0)
    out["prompt_none_no_session"] = {"outcome": run.outcome, "pages": run.pages}
    ok_connect(b, ["t4-owner"])
    run = connect(b, ["t4-owner"], prompt="none", max_pages=0)
    out["prompt_none_live_session"] = {"outcome": run.outcome, "first_page": run.pages[:1]}
    return out


def scenario_negatives():
    out = {}
    b = Browser()
    for name in ("t4-notp", "t4-nogroup", "t4-inactive", "t4-admin"):
        b.clear()
        out[name] = summary(connect(b, [name]))
    b.clear()
    out["wrong_totp"] = summary(connect(b, ["t4-owner"], totp_wrong=True))
    for label, kwargs in {
        "other_client": {"client_id": "other-client"},
        "unregistered_redirect": {"redirect": "dev.animusstudio.alkion.dev:/oauth2redirect/"},
        "near_miss_loopback": {"redirect": "http://localhost:43819/callback"},
        "plain_pkce": {"method": "plain"},
        "missing_pkce": {"omit_challenge": True},
    }.items():
        b.clear()
        ok_connect(b, ["t4-owner"])  # live qualified session: only the request is wrong
        out[label] = summary(connect(b, ["t4-owner"], max_pages=6, **kwargs))
    # Loopback callback succeeds for comparison.
    b.clear()
    out["loopback_callback"] = ok_connect(b, ["t4-owner"], redirect=LOOPBACK)
    # Another flow's login event: default authentication flow, then authorize.
    b.clear()
    out["default_flow_login"] = summary(direct_login(b, ["t4-owner"], flow="default-authentication-flow"))
    out["after_other_flow_event_no_prompt"] = summary(connect(b, ["t4-owner"], max_pages=6))
    out["after_other_flow_event_prompt_login"] = ok_connect(b, ["t4-owner"], prompt="login")
    # prompt=login with an ineligible live session: re-authentication, then denial.
    for name in ("t4-nogroup", "t4-admin"):
        b.clear()
        out[name + "_session_then_prompt_login"] = {
            "direct": summary(direct_login(b, [name], flow="default-authentication-flow")),
            "connect": summary(connect(b, [name], prompt="login"))}
    # prompt=login never rescues a malformed request.
    b.clear()
    ok_connect(b, ["t4-owner"])
    out["prompt_login_plain_pkce"] = summary(connect(b, ["t4-owner"], prompt="login", method="plain", max_pages=6))
    out["prompt_login_unregistered_redirect"] = summary(connect(
        b, ["t4-owner"], prompt="login", redirect="http://localhost:43819/callback", max_pages=6))
    # Replay: park the session after TOTP, finish, then replay the parked cookies.
    b.clear()
    parked = {}

    def park(run, slug, challenge):
        if any(p.endswith("authenticator-validate") for p in run.pages) and "cookies" not in parked:
            parked["cookies"] = b.snapshot()
    first = connect(b, ["t4-owner"], prompt_hook=park)
    out["replay_source"] = summary(first, redeem(b, first, "t4-owner"))
    b.restore(parked["cookies"])
    out["replay_parked_session"] = summary(connect(b, ["t4-owner"], max_pages=2))
    return out


def scenario_slow_ceremony():
    """A ceremony that takes longer than 300 s, paused after both factors."""
    b = Browser()

    def pause(run, slug, challenge):
        if slug == "animus-lan-normal" and any(p.endswith("authenticator-validate") for p in run.pages) \
                and not getattr(run, "paused", False):
            run.paused = True
            time.sleep(305)
    return {"ceremony_over_300s": summary(connect(b, ["t4-slow-a"], prompt_hook=pause))}


def scenario_slow_stale():
    """Same user's session older than 300 s, then Connect with and without prompt=login."""
    b = Browser()
    out = {"login": ok_connect(b, ["t4-slow-b"])}
    wait_for_stale()
    out["stale_no_prompt"] = summary(connect(b, ["t4-slow-b"], max_pages=6))
    out["stale_prompt_login"] = ok_connect(b, ["t4-slow-b"], prompt="login")
    out["after_reauth_prompt_login_again"] = ok_connect(b, ["t4-slow-b"], prompt="login")
    return out


def scenario_slow_other_stale():
    """Another user's session older than 300 s in the same profile, then the owner."""
    b = Browser()
    out = {"other_login": ok_connect(b, ["t4-slow-c-other"])}
    wait_for_stale()
    out["owner_prompt_login_over_stale_other"] = ok_connect(b, ["t4-slow-c"], prompt="login")
    out["owner_retry"] = ok_connect(b, ["t4-slow-c"], prompt="login")
    return out


def scenario_rollback():
    """Applied rollback: previous pages, consent shown again despite stored consent."""
    b = Browser()
    return {"rollback_no_session": ok_connect(b, ["t4-owner"]),
            "rollback_no_session_prompt_login": ok_connect(b, ["t4-other"], prompt="login")}


def scenario_reforward():
    """Forward again after a rollback: consent stored before the rollback still counts."""
    b = Browser()
    return {"reforward_no_session_prompt_login": ok_connect(b, ["t4-owner"], prompt="login")}


SCENARIOS = {"baseline": scenario_baseline, "main": scenario_main, "negatives": scenario_negatives,
             "slow-ceremony": scenario_slow_ceremony, "slow-stale": scenario_slow_stale,
             "slow-other-stale": scenario_slow_other_stale, "rollback": scenario_rollback,
             "reforward": scenario_reforward}

if __name__ == "__main__":
    results = {}
    for name in sys.argv[1:]:
        try:
            results[name] = SCENARIOS[name]()
        except Exception as exc:  # report the class only; never request data
            results[name] = {"harness_error": type(exc).__name__}
    print("T4ACTOR " + json.dumps(results, sort_keys=True))
