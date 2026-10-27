# Function Requests CA overlay v3

The first four own-base workers (run40488464569, runtime5372b666b73e163899f10f76157e1c1485172f3f) built and qualified their environments but did not calibrate any task. All original archives are retained in validation/p07-budget20/function-calibration-v1/.

Gold1963/2148/2317 each has one ordinary TLS failure: direct Session.send() does not consult REQUESTS_CA_BUNDLE. Profilev3 explicitly appends the admitted public fixture CA to the original requests/cacert.pem after exact-base reset and before the checked source snapshot. The original Git blob and fixture hashes are checked; only this fixed trust-material path can change. Original bundle bytes are retained as an exact prefix. The resulting source diff and overlay digest remain in each row. Python implementation, tests, assertions, selected tests and verification flags do not change. Wrong CA rejection remains in real service health. Prior profiles remain immutable.

Controlsv7 separately narrows1963wrong behavior to303redirect handling and2317wrong behavior to byte-method conversion. Earlier wrong controls broke unrelated regressions, so new full triples are required. No previous run is relabelled.

Requests863 remains a separate open environment case: full gold shows Python3 cookie MockRequest.unverifiable incompatibility and additional external endpoint contracts. It is not silently excluded from the30-task denominator. Requests2674 remains open pending its separate Python2 runtime.
