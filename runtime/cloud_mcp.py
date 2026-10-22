"""Minimal stdio MCP for the host-assisted adapter, without Go/CDP or a daemon."""
import json
import sys
from pathlib import Path
from cloud_browser import CloudBrowser, MAX_INPUT, READS, STATUSES
from cloud_host import CloudHost, TOOLS as HOST_TOOLS

PLUGIN_VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()


def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string'}
TOOLS = [
    {'name': 'cloud_browser_status', 'description': 'Report adapter capabilities; does not connect a browser or verify login.', 'inputSchema': schema({})},
    {'name': 'prepare_browser_read', 'description': 'Create a pending public-read request. Host must execute using its supported browser tool. No page is read here.', 'inputSchema': schema({'operation': {'type': 'string', 'enum': list(READS)}, 'arguments': {'type': 'object'}}, ('operation', 'arguments'))},
    {'name': 'get_browser_read', 'description': 'Read saved request/host-reported observations; these are not independently verified.', 'inputSchema': schema({'request_id': STRING}, ('request_id',))},
    {'name': 'record_browser_observation', 'description': 'Persist a real host observation after a supported browser-tool read. Never invent evidence or include credentials.', 'inputSchema': schema({'request_id': STRING, 'observation': schema({'status': {'type': 'string', 'enum': list(STATUSES)}, 'final_url': STRING, 'title': STRING, 'visible_text': STRING, 'evidence_ref': STRING}, ('status', 'final_url', 'title', 'visible_text', 'evidence_ref'))}, ('request_id', 'observation'))}
]


LEGACY_TOOLS = TOOLS
TOOLS = LEGACY_TOOLS + HOST_TOOLS

def serve(config, source=None, sink=None):
    source, sink = source or sys.stdin, sink or sys.stdout
    browser = None
    host = CloudHost(config)
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
                    result = {'protocolVersion': protocol, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'xiaohongshu-cloud-host', 'version': PLUGIN_VERSION}, 'instructions': 'Agent-mediated browser actions. Original platform names return pending host plans, not platform receipts. Host must enforce actual user permission and fresh account identity; record dispatch before any side effect. No autonomous CUA bridge or automatic unknown retry.'}
                    initialized = True
                elif method == 'ping': result = {}
                elif not initialized: raise ValueError('initialize required')
                elif method == 'tools/list': result = {'tools': TOOLS}
                elif method == 'tools/call':
                    try:
                        name, arguments = params.get('name'), params.get('arguments', {})
                        definition = next((tool for tool in TOOLS if tool['name'] == name), None)
                        if definition is None: raise ValueError('ERR_CLOUD_UNSUPPORTED: unsupported tool')
                        if not isinstance(arguments, dict): raise ValueError('tool arguments must be an object')
                        legacy = name in {tool['name'] for tool in LEGACY_TOOLS}
                        if legacy and set(arguments) != set(definition['inputSchema']['properties']): raise ValueError('exact tool arguments required')
                        if name in {'prepare_browser_read','record_browser_observation'}:
                            raise ValueError('ERR_LEGACY_READ_DISABLED: use unified cloud action lifecycle; historical reads cannot bypass pacing, serialization or provenance')
                        if legacy and browser is None: browser = CloudBrowser(config)
                        if name == 'cloud_browser_status': value = browser.status()
                        elif name == 'prepare_browser_read': value = browser.prepare(**arguments)
                        elif name == 'get_browser_read': value = browser.get(**arguments)
                        elif name == 'record_browser_observation': value = browser.record(**arguments)
                        else: value = host.call(name, arguments)
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
        if browser is not None: browser.close()
        host.close()
