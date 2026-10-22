"""Preserve an actually observed public note route without fabricating tokens."""
import re
from urllib.parse import urlsplit


def observed_note_url(operation, payload, url):
    if operation not in {'get_feed_detail','post_comment_to_feed','reply_comment_in_feed'}:
        raise ValueError('ERR_OBSERVED_ROUTE: only note reads/comments accept an observed note URL')
    if not isinstance(url,str) or not url or len(url)>8192 or any(ord(c)<33 for c in url):
        raise ValueError('ERR_OBSERVED_ROUTE: exact observed URL required')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='www.xiaohongshu.com' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:
        raise ValueError('ERR_OBSERVED_ROUTE: official HTTPS note route required')
    feed=payload.get('feed_id')
    # Profile-card links include the observed author profile ID before the note.
    # Validate both identifier segments; never infer the author from our account
    # or strip/rebuild routing parameters. The immutable note ID remains binding.
    profile_route = re.fullmatch(r'/user/profile/([0-9a-f]{24})/([0-9a-f]{24})', parsed.path)
    profile_note_matches = profile_route is not None and profile_route.group(2) == feed
    if not isinstance(feed,str) or not (parsed.path in ('/explore/'+feed,'/discovery/item/'+feed,'/search_result/'+feed) or profile_note_matches):
        raise ValueError('ERR_OBSERVED_ROUTE: observed route must match immutable feed_id')
    # Return byte-for-byte query spelling/order; it may contain required routing
    # parameters absent from the sealed content payload. Do not decode/rebuild it.
    return url


def profile_card_step(binding, args, prior_steps):
    """Host policy for profile browsing, not a claim about platform URL support.

    CUA remains host-mediated: the host must classify native card clicks honestly.
    No permission here can execute CUA or inspect a disguised external call.
    """
    kind=args['kind']; target=args['target']
    if args.get('recovery_evidence') is not None:
        if kind=='read_observation' or (kind=='reload' and args['recovery_evidence'].get('page_reload') is True) or (kind=='click' and args.get('purpose')=='solve_captcha'):
            return
        raise ValueError('ERR_PROFILE_CARD_ONLY: recovery cannot navigate away from profile cards')
    if kind=='navigation':
        if target!=binding['target']['url']:
            raise ValueError('ERR_PROFILE_CARD_ONLY: only initial bound profile URL may be opened')
        if any(step['kind']=='navigation' and step['status'] in {'completed','in_flight','unknown'} for step in prior_steps):
            raise ValueError('ERR_PROFILE_CARD_ONLY: profile already opened; retain native page and cards')
    elif kind=='card_read':
        if not re.fullmatch(r'profile-card:[0-9a-f]{24}',target):
            raise ValueError('ERR_PROFILE_CARD_ONLY: native card read requires observed note ID')
    elif kind not in {'click','scroll','media_request','read_observation'}:
        raise ValueError('ERR_PROFILE_CARD_ONLY: no search or direct-note fallback inside history read')


def search_card_step(binding, args, prior_steps):
    """Search query/filter budgets stay intact; cards are separate read debits."""
    kind=args['kind'];target=args['target']
    if args.get('recovery_evidence') is not None:
        if kind=='read_observation' or (kind=='reload' and args['recovery_evidence'].get('page_reload') is True) or (kind=='click' and args.get('purpose')=='solve_captcha'):
            return
        raise ValueError('ERR_SEARCH_CARD_ONLY: recovery cannot switch routes')
    if kind=='navigation':
        raise ValueError('ERR_SEARCH_CARD_ONLY: initial query uses search; details stay in native cards')
    if kind=='card_read':
        if not re.fullmatch(r'search-card:[0-9a-f]{24}',target):
            raise ValueError('ERR_SEARCH_CARD_ONLY: native card requires observed note ID')
    elif kind=='search':
        if '://' in target:
            if target!=binding['target']['url']:
                raise ValueError('ERR_SEARCH_CARD_ONLY: only bound initial query URL')
            if any(step['kind']=='search' and step['status'] in {'completed','in_flight','unknown'} for step in prior_steps):
                raise ValueError('ERR_SEARCH_CARD_ONLY: query already issued; no URL replay')
        # Native query/filter interactions use observed inert control labels.
        # Every one still uses the existing search budget, never generic clicks.
    elif kind not in {'click','scroll','media_request','read_observation'}:
        raise ValueError('ERR_SEARCH_CARD_ONLY: unsupported search card operation')


def observed_comment_entry(url):
    if not isinstance(url,str) or len(url)>8192 or any(ord(c)<33 for c in url):
        raise ValueError('ERR_COMMENT_CARD_ENTRY: exact observed entry URL required')
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname!='www.xiaohongshu.com' or p.username or p.password or p.port not in (None,443) or p.fragment:
        raise ValueError('ERR_COMMENT_CARD_ENTRY: official HTTPS entry only')
    if p.path!='/search_result' and not re.fullmatch(r'/user/profile/[0-9a-f]{24}',p.path):
        raise ValueError('ERR_COMMENT_CARD_ENTRY: search results or profile entry only; never detail URL')
    return url


def comment_card_step(binding,args,prior_steps):
    target=binding['target'];kind=args['kind'];value=args['target']
    if args.get('recovery_evidence') is not None:
        if kind=='read_observation' or (kind=='click' and args.get('purpose')=='solve_captcha'):return
        raise ValueError('ERR_COMMENT_CARD_ENTRY: preserve comment scene; no recovery navigation')
    if kind=='search' and value.startswith('native-filter:') and '://' not in value:
        if urlsplit(target['url']).path!='/search_result' or not any(s['kind']=='search' and s['status']=='completed' for s in prior_steps):
            raise ValueError('ERR_COMMENT_CARD_ENTRY: native filter requires completed bound search entry')
        return
    if kind in {'search','navigation'}:
        if value!=target['url']:
            raise ValueError('ERR_COMMENT_CARD_ENTRY: only exact observed entry; no detail URL or other search')
        expected='search' if urlsplit(target['url']).path=='/search_result' else 'navigation'
        if kind!=expected:raise ValueError('ERR_COMMENT_CARD_ENTRY: entry must use correct search/read budget')
        if any(s['kind'] in {'search','navigation'} and s['status'] in {'completed','in_flight','unknown'} for s in prior_steps):
            raise ValueError('ERR_COMMENT_CARD_ENTRY: entry already opened')
    elif kind=='card_read':
        if value!='comment-card:'+binding['payload']['feed_id']:
            raise ValueError('ERR_COMMENT_CARD_ENTRY: native card must match sealed feed_id')
    elif kind not in {'click','read_observation','scroll','media_request'}:
        raise ValueError('ERR_COMMENT_CARD_ENTRY: unsupported step')
    if args.get('purpose')=='submit_comment' and not any(s['kind']=='card_read' and s['status']=='completed' for s in prior_steps):
        raise ValueError('ERR_COMMENT_CARD_ENTRY: complete bound native card read before Send')
