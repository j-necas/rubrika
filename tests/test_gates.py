import pytest
from rubrika.gates import is_bot_gate

@pytest.mark.parametrize('body',[
    b'<html><script src="/__uzdbm_x.js"></script>Verifying your browser before proceeding</html>',
    b'<html><body><form method="post" action="/app/human/verify"><input type="hidden" name="nonce" value="0">'
    b'<input type="hidden" name="difficulty" value="15"></form><script>solve()</script></body></html>',
    b'<html><title>Just a moment...</title><div id="cf-chl-widget"></div></html>',
    b'<html><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/x"></script></html>',
    b'<html><body><form action="/check"><div class="g-recaptcha" data-sitekey="abc"></div></form></body></html>',
    b'<html><body><div class="h-captcha" data-sitekey="abc"></div></body></html>',
])
def test_known_gates(body):
    assert is_bot_gate(body,content_type='text/html')

def test_redirect_to_challenge_is_a_gate():
    assert is_bot_gate(b'','/digitalnibadatelna/human/challenge')

@pytest.mark.parametrize('body',[
    b'<html><a href="Zoomify.action?captcha=">Ulozit</a> captcha recaptcha</html>',
    '<html><table><tr><td>Matrika N 1784–1800</td></tr></table></html>'.encode(),
    b'<html><form action="/search"><input name="term"></form></html>',
    b'',
])
def test_ordinary_pages_are_not_gates(body):
    assert not is_bot_gate(body,content_type='text/html')

def test_large_page_with_captcha_widget_is_not_a_gate():
    body=b'<html><body>'+b'<p>obsah</p>'*5000+b'<div class="g-recaptcha" data-sitekey="k"></div></body></html>'
    assert not is_bot_gate(body,content_type='text/html')


def test_cloudflare_jsd_script_is_not_a_gate():
    body=b'<html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script><p>SYNTHETIC</p></html>'
    assert not is_bot_gate(body,content_type='text/html')

def test_captcha_widget_with_reversed_attribute_order_is_a_gate():
    body=b'<html><body><div data-sitekey="k" class="g-recaptcha"></div></body></html>'
    assert is_bot_gate(body,content_type='text/html')
    assert is_bot_gate(b'<div data-sitekey="k" data-theme="x" class="cf-turnstile x"></div>',content_type='text/html')

def test_just_a_moment_with_cf_chl_is_a_gate():
    assert is_bot_gate(b'<html><title>Just a moment...</title><script>window._cf_chl_opt={}</script></html>',content_type='text/html')
