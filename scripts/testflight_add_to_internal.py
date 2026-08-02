#!/usr/bin/env python3
"""Answer export compliance + add a build to the Internal Testers group via
the App Store Connect API — replaces the two manual per-build clicks on the
ASC website. Usage: python3 scripts/testflight_add_to_internal.py <build_number>

Retries the group-assignment call for a few minutes: ASC has a known
propagation lag between a build reaching processingState=VALID and it
becoming visible to the beta-group relationship endpoints.
"""
import sys
import time
import json
import urllib.request

KEY_ID = "422MKHNWD5"
ISSUER_ID = "c4c8d671-d14d-48b8-a605-94c23a63b2fa"
KEY_PATH = "/Users/home/.appstoreconnect/private_keys/AuthKey_422MKHNWD5.p8"
APP_ID = "6787590830"                    # com.getmarque.app
INTERNAL_GROUP_ID = "c811be00-cd4c-4db6-ace5-38ac0d77377a"   # "Internal Testers"


def _token():
    import jwt
    pk = open(KEY_PATH).read()
    return jwt.encode(
        {"iss": ISSUER_ID, "iat": int(time.time()), "exp": int(time.time()) + 900,
         "aud": "appstoreconnect-v1"},
        pk, algorithm="ES256", headers={"kid": KEY_ID})


def _req(path, method="GET", body=None, headers=None):
    h = {"Authorization": f"Bearer {_token()}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"https://api.appstoreconnect.apple.com{path}",
                                  data=data, headers=h, method=method)
    with urllib.request.urlopen(req) as r:
        if r.status == 204:
            return None
        raw = r.read()
        return json.loads(raw) if raw else None


def find_build(version: str) -> str:
    d = _req(f"/v1/builds?filter[app]={APP_ID}&filter[version]={version}")
    if not d["data"]:
        raise SystemExit(f"no build with version {version} found on ASC")
    return d["data"][0]["id"]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: testflight_add_to_internal.py <build_number>")
    version = sys.argv[1]
    build_id = find_build(version)
    print(f"build {version} -> {build_id}")

    try:
        _req(f"/v1/builds/{build_id}", method="PATCH",
             body={"data": {"type": "builds", "id": build_id,
                            "attributes": {"usesNonExemptEncryption": False}}})
        print("export compliance answered (usesNonExemptEncryption=false)")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print("export compliance already answered — skipping")
        else:
            raise

    deadline = time.time() + 900
    while True:
        try:
            _req(f"/v1/builds/{build_id}/relationships/betaGroups", method="POST",
                 body={"data": [{"type": "betaGroups", "id": INTERNAL_GROUP_ID}]})
            print("added to Internal Testers")
            return
        except urllib.error.HTTPError as e:
            if time.time() >= deadline:
                raise
            time.sleep(20)


if __name__ == "__main__":
    main()
