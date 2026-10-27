"""Fixed network fixtures for the historical suite; no target imports or grading.

These local services exercise request/response protocols. They do not replay or
claim fidelity to today's external websites, and cannot inspect a candidate.
"""
from __future__ import annotations

HOSTS = ("httpbin.org", "github.com", "kennethreitz.com", "www.google.com", "blip.fm",
         "t.co", "tinyurl.com", "feedproxy.google.com", "www.dealipedia.com", "www.marketwire.com")

SOURCE = r'''import hashlib,json,os,re,urllib
from werkzeug.http import parse_dict_header
fixture_nonce=os.urandom(24).encode('hex')
fixture_opaque=os.urandom(24).encode('hex')
fixture_realm='p07-public-digest-fixture'
market_destination='/press-release/jp-morgan-behauptet-sich-der-spitze-europ\xe4ischer-anleihe-analysten-laut-umfrageergebnissen-1628202.htm'
def fixture_redirect(environ,start_response,destination):
    start_response('302 Found',[('Location',destination),('Content-Length','0')])
    return [b'']
def fixture_digest(environ,start_response):
    path=environ.get('PATH_INFO','')
    parts=path.strip('/').split('/')
    if len(parts)!=4 or parts[0]!='digest-auth' or parts[1]!='auth':
        start_response('404 Not Found',[('Content-Length','0')]);return [b'']
    user,password=parts[2:]
    header=environ.get('HTTP_AUTHORIZATION','')
    credentials=parse_dict_header(header[7:]) if header.startswith('Digest ') else {}
    uri=path+('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')
    required=('username','realm','nonce','uri','response','qop','nc','cnonce','opaque')
    valid=(all(name in credentials for name in required)
           and credentials['username']==user and credentials['realm']==fixture_realm
           and credentials['nonce']==fixture_nonce and credentials['opaque']==fixture_opaque
           and credentials['uri']==uri and credentials['qop']=='auth'
           and re.match(r'^[0-9a-fA-F]{8}$',credentials['nc']) is not None)
    if valid:
        h=lambda value:hashlib.md5(value.encode('utf-8') if isinstance(value,unicode) else value).hexdigest()
        ha1=h('%s:%s:%s'%(user,fixture_realm,password))
        ha2=h('%s:%s'%(environ['REQUEST_METHOD'],uri))
        expected=h('%s:%s:%s:%s:auth:%s'%(ha1,fixture_nonce,credentials['nc'],credentials['cnonce'],ha2))
        valid=credentials['response']==expected
    if not valid:
        challenge='Digest realm="%s", nonce="%s", opaque="%s", qop="auth", algorithm=MD5'%(fixture_realm,fixture_nonce,fixture_opaque)
        start_response('401 Unauthorized',[('WWW-Authenticate',challenge),('Content-Length','0')])
        return [b'']
    body=json.dumps({'authenticated':True,'user':user}).encode('utf-8')
    start_response('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(body)))])
    return [body]
def historical_application(environ,start_response):
    host=environ.get('HTTP_HOST','').split(':',1)[0]
    path=environ.get('PATH_INFO','')
    environ.setdefault('CONTENT_TYPE','')
    if environ.get('HTTP_TRANSFER_ENCODING','').lower()=='chunked':
        environ['wsgi.input_terminated']=True
    if host=='httpbin.org' and path.startswith('/digest-auth/'):
        return fixture_digest(environ,start_response)
    if host=='httpbin.org' and path=='/response-headers':
        query=environ.get('QUERY_STRING','')
        if '=' not in query:
            decoded=urllib.unquote(query)
            name,separator,value=decoded.partition(':')
            if separator and re.match(r'^[A-Za-z0-9-]+$',name) and '\r' not in value and '\n' not in value:
                environ['QUERY_STRING']=urllib.urlencode([(name,value.lstrip())])
    if host=='www.google.com' and path=='/':environ['PATH_INFO']='/post' if environ['REQUEST_METHOD']=='POST' else '/get'
    if host=='blip.fm' and path=='/~1abvfu':environ['PATH_INFO']='/encoding/utf8'
    if host=='t.co' and path=='/NFrx0zLG':
        return fixture_redirect(environ,start_response,'http://httpbin.org/get')
    if host=='tinyurl.com' and path=='/preview.php':
        environ['PATH_INFO']='/cookies/set/preview/0';environ['QUERY_STRING']=''
    if host=='tinyurl.com' and path=='/7zp3jnr':
        return fixture_redirect(environ,start_response,'http://httpbin.org/cookies')
    if host=='feedproxy.google.com' and path=='/~r/DealipediaNews/~3/BQtUJRJeZlo/deal_view_investment.php':
        return fixture_redirect(environ,start_response,'http://www.dealipedia.com/deal_view_investment.php?r=20012')
    if host=='www.dealipedia.com' and path=='/deal_view_investment.php':environ['PATH_INFO']='/get'
    if host=='www.marketwire.com' and path=='/mw/release.do':
        return fixture_redirect(environ,start_response,'http://www.marketwire.com'+market_destination)
    if host=='www.marketwire.com' and path.startswith('/press-release/'):
        environ['PATH_INFO']='/encoding/utf8'
    def fixture_response(status,headers,exc_info=None):
        if host=='github.com' and path=='/':
            headers=list(headers)+[('Set-Cookie','p07_fixture=public; Path=/; HttpOnly')]
        return start_response(status,headers,exc_info)
    return httpbin.app(environ,fixture_response)
'''
