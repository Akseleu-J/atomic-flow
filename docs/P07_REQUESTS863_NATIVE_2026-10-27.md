# Requests863 native environment

Prospective, not calibrated. Last open calibration; union remains29/30. No provider calls or spend.

Uses its own pinned official863 base, the separately qualified immutable Python2.7.13Wheezy donor (real SSLv3 context),18 verified publisher wheels and291 installed files. Dedicated profile binds all65 corpus-v3 requirement/regression IDs. Host preparation verifies publisher bytes/ABI/closure without target imports or setup.

The historical suite requires HTTP1.1 persistence, chunked input, cookies across github.com/httpbin.org and TLS. Use actual Gunicorn19.9.0 gthread/futures3.3.0 serving original HTTPBin0.5.0. Both names resolve to loopback in network=none, with a dual-DNS-SAN fixture certificate. A disclosed host-owned WSGI middleware adds one public fixture cookie only to the github.com root response; this exercises cookie/domain persistence, and makes no claim to reproduce live GitHub. No target API, source assertion, collection or required ID is changed.

Before exposing target source, health verifies real HTTP/CA-HTTPS/auth/wrong-CA rejection, root cookie, closed port1, two requests on the same persistent connection with correct close behavior and a real chunkedPOST body. Servers run as separate private Python process groups; their exit/absence is required even on pytest failure. Full base/gold/wrong and archived raw-output audit remain required.

Attempt1 CI40499615004 onef48cf5 provisioned the private runtime, then stopped on real chunked-body health before target execution; cleanup confirmed in all3 rows. Artifact12732475720 SHA2566747afad25230efa1a4f460adc31f0192411212cd84bd0e76fa3c4e0fda61789 retained. Publisher source explains the mismatch: Gunicorn19.9 supplies its decoded EOF-delimited body, but does not set wsgi.input_terminated; Werkzeug0.11.4 returns an empty input in that case. Prospective service-v2 adds the standard WSGI termination flag only for actual chunked requests; no request/body/response is fabricated and no target API changed. A fresh complete calibration is required.
