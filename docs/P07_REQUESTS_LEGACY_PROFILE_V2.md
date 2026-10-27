# Requests3362 prospective dependency profile v2

Prepared27 October2026 from retained full-suite failures and read-only primary
publisher wheel inspection. The original13-wheel profile remains unchanged.
This v2 profile is **not qualified or calibrated** and admits only a prospective
Requests3362 environment, not the other Requests targets or the separate
function-service profile still bound to v1.

File: benchmarks/stage4/p07-requests-legacy-profile-v2.json.
Git-LF SHA256:
810e98b8f663ed03c3f4b529f692fdcdcffe46364ee678ac18fcda547ad1076f.

## Concrete trigger and minimal candidate

Workflow40453010247 on runtime5d57a0f3b4762c40be6c3441e63dfb524fe5e511
collected349 tests in every row and observed all76 required IDs after canonical
node-ID normalization. Gold passed the protected requirement but still had nine
ordinary failures: eight missing SOCKS dependency failures, and one HTTPBin
response-headers JSON error. The attempt remains unaccepted. Its artifact is
12711726622, archive SHA256
7a65e55c455da39e3e1da0d56b4b258aeb7ec9d26915b05a4802fdf5f4d4da50.

The prospective candidate changes package inputs only:

| Package | v1 | v2 | Source-backed reason |
| --- | --- | --- | --- |
|HTTPBin|0.4.1|0.5.0|Publisher response_headers calls jsonify(list(headers.lists())), avoiding the generator serialization failure under selected Flask0.12.5|
|PySocks|absent|1.7.1 py3-none-any|Supplies the missing socks module; actual wheel has no active or declared Requires-Dist|

The other12 original wheel records and versions are unchanged. There are14
wheels totalling1,164,263 bytes. No service response shim, target monkeypatch,
stdlib replacement, hidden test edit, skipped test or new classification rule
is proposed. Model calls in this audit are zero.

## Primary immutable wheel inputs

| Input | SHA256 |
| --- | --- |
|httpbin-0.5.0-py2.py3-none-any.whl|710069973216d4bbf9ab6757f1e9a1f3be05832ce77da023adce0a98dfeecfee|
|HTTPBin wheel METADATA|cda4cc85b8df7c0dcfa8ce94ad96595029390f8bb4769c20b719ec55dcd7acc1|
|httpbin/core.py|672b3b55ba56292e4fb30fa63bec70eb2b923b0a212afae1ac3e2347752fcf59|
|httpbin/__init__.py|bc1dd3503b3b1adb087091d8bed409e9e174c39f86cb6fd5e87fbf8496e641d8|
|PySocks-1.7.1-py3-none-any.whl|2725bd0a9925919b9b51739eea5f9e2bae91e83288108a9ad338b2e3a4435ee5|
|PySocks wheel METADATA|cdb40c8b38cf38e3f80e1122117db85d78cdad8b88c45f54194a4dd2e14307a6|
|socks.py|c4e627dbbb7d206adb4c1cd6b1452e3dad1806ea6582253293390e0795798856|

The profile records exact publisher URLs, complete PyPI metadata-response hashes,
wheel sizes, hashes and actual wheel METADATA. Every wheel was downloaded and
SHA-checked, its ZIP decoded, and source/metadata read without executing package
code. These facts do not prove installation or runtime behavior.

HTTPBin0.5.0's actual Requires-Dist entries are Flask,MarkupSafe,decorator,
itsdangerous and six. The existing selected versions satisfy these unbounded
edges. The pytest-httpbin edge now selects HTTPBin0.5.0. PySocks declares
Requires-Python>=2.7 excluding3.0,3.1,3.2 and3.3, and no Requires-Dist; its selected
py3 wheel admits Python3.6. No extra closure packages are introduced by these
changes beyond PySocks itself. Inactive extras/platform requirements remain
recorded from the earlier profile.

The complete publisher installed-byte inventory has238 files and digest
34a4e8aefd72dcfce63b74100b4bc449fb3d12b11845df1873b50a7b149cf702.
The digest covers UTF8 compact JSON with lexically sorted relative file keys,
no trailing newline. Inventory extraction retains wheel-root modules/assets and
each dist-info/METADATA; it excludes directory entries,.data paths and other
dist-info files rewritten by pip. This is the expected original-wheel inventory,
not a claim that these files have been installed. The certificate overlay must
retain its separately disclosed three changed hashes after installation.

## Endpoint and plugin limits

The0.5.0 wheel still exports its core application from httpbin.__init__, matching
the old plugin import shape. Source route comparison retains every0.4.1 route
path. The redirect-to route adds accepted methods while retaining GET, and an
additional digest-auth algorithm route appears. Those are source observations,
not measured endpoint equivalence. Real response-headers multi-value/cookie,
HTTP/CA-HTTPS, authentication and other full-suite behaviors still require the
fresh worker and complete tests.

The old pytest2.8.7/pytest-httpbin0.2.0 loading contract is unchanged. Rebuild the
exact own-base runtime with the new closed wheel set, verify ABI and every
installed byte, create a fresh separately disclosed CN127.0.0.1/no-SAN certificate
layer, and repeat explicit plugin plus real HTTP/CA-HTTPS/wrong-CA health. Integrate
the qualified timeout route/DROP into the same fresh target worker as before.

Rerun the entire protected base/gold/wrong suite and preserve all ordinary
observations, native exits, source hashes, missing/skip/error outcomes and cleanup.
Do not reinterpret the previous nine-failure gold as success after this proposal.
The existing function-service profile remains frozen against v1 until a separate
review explicitly admits a different package set for those own-base targets.

## Requests3362 accepted: inventory25/30

Run `40486056394` on `8ed3abf2b9a5cd50798be6a487b1c55baf589a7d`
completed successfully. Artifact `12725386677` SHA256
`33cd9262cf0a48e6f2fbb2e0dee0e1536084535f2f237cd3ffa0810e3e267928`
and all three raw stdout/stderr hashes checked independently. Every row retains
349 ordinary tests and all76 required IDs; native exits1/0/1, base/wrong fail
only the protected requirement, gold passes all76, all75P2P pass throughout.
Native suite non-obligatory skip/XFAIL/XPASS remain explicit, never requiredPASS.
Same-worker namespace,3DROPpackets per row,noexternalroute, sourcepreservation
and cleanup confirmed. Qualified14wheelv2,TLS,plugins and complete opaque-ID
parser maps are retained. Earlier failed attempts remain unchanged.

AllCIon8ed3abf green. Verified hostarchives:2043PASS/14SKIP eachPython3.11/3.12,
Windows4PASS,335sourcehashes andPR/source tree equality. New model calls0.
Five Requests remain OPEN:863,1963,2148,2317,2674. FullP07 also still requires
TaskRunner check/repair wiring, matchedbaseline/atomic, holdout and ablations.
