import pytest
from rubrika.robots import Robots

UA = "rubrika/1.0 (+https://github.com/j-necas/rubrika)"

def test_wildcards_work_on_every_python():
    r = Robots.parse("User-agent: *\nAllow: /DA*\nDisallow: /pages/*\nDisallow: /oai/*\n")
    assert not r.allowed(UA, "/pages/search.php?q=x") and not r.allowed(UA, "/oai/list")
    assert r.allowed(UA, "/DA?menu=3&id=1") and r.allowed(UA, "/view.php?id=1")

def test_dollar_anchors_end():
    r = Robots.parse("User-agent: *\nDisallow: /*.pdf$\n")
    assert not r.allowed(UA, "/x/a.pdf") and r.allowed(UA, "/x/a.pdf?download=1")

def test_longest_match_wins_and_allow_wins_ties():
    r = Robots.parse("User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /c\nAllow: /c\n")
    assert r.allowed(UA, "/a/b/c") and not r.allowed(UA, "/a/x") and r.allowed(UA, "/c")

def test_specific_group_beats_star_and_same_agent_groups_merge():
    r = Robots.parse("User-agent: *\nDisallow: /\n\nUser-agent: Rubrika\nDisallow: /private\n\nUser-agent: rubrika\nAllow: /p\n")
    assert r.allowed(UA, "/public") and not r.allowed(UA, "/private/x")
    assert not r.allowed("otherbot/1.0", "/public")

def test_crawl_delay_exact_and_falls_back_to_star():
    assert Robots.parse("User-agent: *\nCrawl-delay: 120\n").crawl_delay(UA) == 120.0
    r = Robots.parse("User-agent: *\nCrawl-delay: 10\n\nUser-agent: rubrika\nDisallow: /x\n")
    assert r.crawl_delay(UA) == 10.0 and not r.allowed(UA, "/x")
    assert Robots.parse("User-agent: rubrika\nCrawl-delay: 3\n\nUser-agent: *\nCrawl-delay: 10\n").crawl_delay(UA) == 3.0
    assert Robots.parse("User-agent: *\nCrawl-delay: soon\n").crawl_delay(UA) is None
    assert Robots.empty().crawl_delay(UA) is None and Robots.empty().allowed(UA, "/x")

def test_consecutive_user_agents_share_rules_and_comments_are_ignored():
    r = Robots.parse("# c\nUser-agent: a\nUser-agent: *  # both\nDisallow: /x # tail\n")
    assert not r.allowed(UA, "/x") and not r.allowed("a", "/x/y")

def test_disallow_all_empty_disallow_and_robots_itself():
    assert not Robots.parse("User-agent: *\nDisallow: /\n").allowed(UA, "/anything")
    assert Robots.parse("User-agent: *\nDisallow:\n").allowed(UA, "/anything")
    assert Robots.parse("User-agent: *\nDisallow: /\n").allowed(UA, "/robots.txt")

def test_rules_before_any_user_agent_are_ignored():
    assert Robots.parse("Disallow: /\nUser-agent: *\nAllow: /\n").allowed(UA, "/x")

@pytest.mark.parametrize("rules,target,allowed", [
    ("Disallow: /\nAllow: /private%2Fpublic", "/private/public", False),   # %2F is not a path separator
    ("Disallow: /\nAllow: /private%2Fpublic", "/private%2fpublic", True),  # hex case is normalised
    ("Disallow: /x%2Ay", "/xZZy", True),                                 # encoded * is a literal, not a wildcard
    ("Disallow: /x%2Ay", "/x*y", True),
    ("Disallow: /x%2Ay", "/x%2Ay", False),
    ("Disallow: /a%24", "/a$", True),                                    # encoded $ is a literal
    ("Disallow: /%C5%BE", "/ž/x", False),                                # non-ASCII is compared UTF-8 percent-encoded
    ("Disallow: /ž", "/%C5%BE", False),
    ("Disallow: /%7Euser", "/~user", False),                             # unreserved characters are decoded
    ("Disallow: /%C5%BE\nAllow: /%C5%BEa", "/ža", True),                 # length counts octets of the encoded form
])
def test_percent_encoding_per_rfc9309(rules, target, allowed):
    assert Robots.parse("User-agent: *\n" + rules + "\n").allowed(UA, target) is allowed

def test_utf8_bom_does_not_drop_first_group():
    r = Robots.parse("\ufeffUser-agent: *\nDisallow: /\n")
    assert not r.allowed(UA, "/x")

@pytest.mark.parametrize("value", ["inf", "nan", "-inf", "Infinity"])
def test_non_finite_crawl_delay_is_ignored(value):
    assert Robots.parse(f"User-agent: *\nCrawl-delay: {value}\n").crawl_delay(UA) is None
