import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlencode
from urllib.request import HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, Request, parse_http_list, parse_keqv_list

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_requests863_wire_contracts as wire  # noqa:E402


def contracts():
    functions = [node for node in ast.parse(wire.SOURCE).body if isinstance(node, ast.FunctionDef)]
    namespace = {'hashlib': hashlib, 'json': json, 're': re, 'unicode': str,
                 'fixture_nonce': 'a' * 48, 'fixture_opaque': 'b' * 48, 'fixture_realm': 'p07-public-digest-fixture',
                 'parse_dict_header': lambda value: parse_keqv_list(parse_http_list(value))}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<host-owned-wire-contracts>', 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('failure', [None, 'password', 'nonce', 'uri', 'response', 'missing'])
def test_standard_library_digest_client_must_supply_a_real_valid_response(failure):
    namespace = contracts()
    path = '/digest-auth/auth/user/pass'
    url = 'http://httpbin.org' + path
    passwords = HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, url, 'user', 'wrong' if failure == 'password' else 'pass')
    handler = HTTPDigestAuthHandler(passwords)
    challenge = {'realm': namespace['fixture_realm'], 'nonce': namespace['fixture_nonce'],
                 'opaque': namespace['fixture_opaque'], 'qop': 'auth', 'algorithm': 'MD5'}
    header = 'Digest ' + handler.get_authorization(Request(url), challenge)
    if failure == 'nonce':
        header = header.replace('a' * 48, 'c' * 48)
    elif failure == 'uri':
        header = header.replace(path, '/different-uri')
    elif failure == 'response':
        header = re.sub(r'response="[0-9a-f]+"', 'response="' + '0' * 32 + '"', header)
    elif failure == 'missing':
        header = ''
    environ = {'PATH_INFO': path, 'REQUEST_METHOD': 'GET', 'HTTP_AUTHORIZATION': header}
    observed = []
    body = namespace['fixture_digest'](environ, lambda status, fields: observed.append((status, dict(fields))))
    assert observed[0][0] == ('200 OK' if failure is None else '401 Unauthorized')
    assert 'Set-Cookie' not in observed[0][1]
    if failure is None:
        assert json.loads(b''.join(body)) == {'authenticated': True, 'user': 'user'}
    else:
        assert observed[0][1]['WWW-Authenticate'].startswith('Digest ')


def test_legacy_redirect_contains_real_nonascii_wire_byte_and_no_response_rewrite():
    tree = ast.parse(wire.SOURCE)
    destination = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'market_destination' for target in node.targets))
    assert '\xe4' in destination and '%C3%A4' not in destination
    namespace = contracts()
    observed = []
    response = namespace['fixture_redirect']({}, lambda status, headers: observed.append((status, headers)), destination)
    assert response == [b''] and observed == [('302 Found', [('Location', destination), ('Content-Length', '0')])]


@pytest.mark.parametrize('value', ['Set-Cookie: key=value; Path=/', 'Set-Cookie: key=value\r\nInjected: true'])
def test_legacy_query_translation_refuses_header_injection(value):
    from types import SimpleNamespace

    namespace = contracts()
    observed = []
    namespace.update(urllib=SimpleNamespace(unquote=unquote, urlencode=urlencode),
                     httpbin=SimpleNamespace(app=lambda environ, respond: observed.append(environ['QUERY_STRING']) or []))
    encoded = __import__('urllib.parse', fromlist=['quote']).quote(value, safe='')
    environ = {'HTTP_HOST': 'httpbin.org', 'PATH_INFO': '/response-headers', 'QUERY_STRING': encoded}
    namespace['historical_application'](environ, lambda *args: None)
    assert observed == ([encoded] if '\r' in value else [urlencode([('Set-Cookie', 'key=value; Path=/')])])
