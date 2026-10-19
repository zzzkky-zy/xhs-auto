"""Minimal stdio MCP for the host-assisted adapter, without Go/CDP or a daemon."""
import json
import sys
from cloud_browser import CloudBrowser, MAX_INPUT, READS, STATUSES


def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string'}
TOOLS = [
    {'name': 'cloud_browser_status', 'description': 'Report adapter capabilities; does not connect a browser or verify login.', 'inputSchema': schema({})},
    {'name': 'prepare_browser_read', 'description': 'Create a pending public-read request. Host must execute using its supported browser tool. No page is read here.', 'inputSchema': schema({'operation': {'type': 'string', 'enum': list(READS)}, 'arguments': {'type': 'object'}}, ('operation', 'arguments'))},
    {'name': 'get_browser_read', 'description': 'Read saved request/host-reported observations; these are not independently verified.', 'inputSchema': schema({'request_id': STRING}, ('request_id',))},
    {'name': 'record_browser_observation', 'description': 'Persist a real host observation after a supported browser-tool read. Never invent evidence or include credentials.', 'inputSchema': schema({'request_id': STRING, 'observation': schema({'status': {'type': 'string', 'enum': list(STATUSES)}, 'final_url': STRING, 'title': STRING, 'visible_text': STRING, 'evidence_ref': STRING}, ('status', 'final_url', 'title', 'visible_text', 'evidence_ref'))}, ('request_id', 'observation'))}
]


def serve(config, source=None, sink=None):
    source, sink = source or sys.stdin, sink or sys.stdout
    browser = CloudBrowser(config)
    initialized = False
    try:
        while True:
            line = source.readline(MAX_INPUT + 1)
            if not line: break
            if len(line) > MAX_INPUT:
                # A too-large JSON line is never parsed or echoed.
                while line and not line.endswith('\n'): line = source.readline(MAX_INPUT + 1)
                sink.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'input too large'}}) + '\n'); sink.flush(); continue
            request = None
            try:
                try:
                    request = json.loads(line)
                except (json.JSONDecodeError, RecursionError):
                    sink.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'invalid JSON'}}) + '\n'); sink.flush(); continue
                if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str): raise ValueError('invalid JSON-RPC request')
                if 'id' not in request: continue
                if type(request['id']) not in (str, int):
                    sink.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'id must be a string or integer'}}) + '\n'); sink.flush(); continue
                method, params = request['method'], request.get('params', {})
                if not isinstance(params, dict): raise ValueError('params must be an object')
                if method == 'initialize':
                    protocol = params.get('protocolVersion')
                    if protocol not in {'2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'}: protocol = '2025-11-25'
                    result = {'protocolVersion': protocol, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'xiaohongshu-cloud-host', 'version': '0.6.0-cloud-host.1'}, 'instructions': 'Agent-mediated public reads only. Prepared requests remain pending until host browser tools actually run; no CDP, unattended operation or account-write support.'}
                    initialized = True
                elif method == 'ping': result = {}
                elif not initialized: raise ValueError('initialize required')
                elif method == 'tools/list': result = {'tools': TOOLS}
                elif method == 'tools/call':
                    try:
                        name, arguments = params.get('name'), params.get('arguments', {})
                        definition = next((tool for tool in TOOLS if tool['name'] == name), None)
                        if definition is None: raise ValueError('ERR_CLOUD_UNSUPPORTED: unsupported tool')
                        if not isinstance(arguments, dict) or set(arguments) != set(definition['inputSchema']['properties']): raise ValueError('exact tool arguments required')
                        if name == 'cloud_browser_status': value = browser.status()
                        elif name == 'prepare_browser_read': value = browser.prepare(**arguments)
                        elif name == 'get_browser_read': value = browser.get(**arguments)
                        else: value = browser.record(**arguments)
                        result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
                    except (ValueError, KeyError, TypeError) as error:
                        result = {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}
                else:
                    sink.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'method not found'}}) + '\n'); sink.flush(); continue
                response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
            except (ValueError, KeyError, TypeError) as error:
                response = {'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) and type(request.get('id')) in (str, int) else None, 'error': {'code': -32600, 'message': str(error)}}
            sink.write(json.dumps(response, ensure_ascii=False) + '\n'); sink.flush()
    finally:
        browser.close()
