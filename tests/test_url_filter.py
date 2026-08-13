import pytest

from src.url_filter import URLFilter


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com/path?value=1#part",
        "HTTPS://EXAMPLE.COM:8443/page",
    ],
)
def test_valid_http_urls_are_allowed_by_default(url: str) -> None:
    assert URLFilter().should_crawl(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "/relative/path",
        "example.com/page",
        "mailto:test@example.com",
        "javascript:void(0)",
        "ftp://example.com/file",
        "https:///missing-host",
        "https://example.com:invalid/path",
    ],
)
def test_invalid_or_unsupported_urls_are_rejected(url: str) -> None:
    assert URLFilter().should_crawl(url) is False


def test_same_domain_only_uses_hostname_not_case_or_port() -> None:
    url_filter = URLFilter(
        same_domain_only=True,
        allowed_domains={"Example.COM:443"},
    )

    assert url_filter.should_crawl("https://example.com/page") is True
    assert url_filter.should_crawl("http://EXAMPLE.COM:8080/other") is True
    assert url_filter.should_crawl("https://other.example/page") is False


def test_subdomain_is_not_the_same_exact_domain() -> None:
    url_filter = URLFilter(
        same_domain_only=True,
        allowed_domains={"example.com"},
    )

    assert url_filter.should_crawl("https://docs.example.com/page") is False


def test_filter_can_build_allowed_domains_from_multiple_start_urls() -> None:
    url_filter = URLFilter.from_start_urls(
        ["https://one.example/start", "https://two.example:8443/start"],
        same_domain_only=True,
    )

    assert url_filter.allowed_domains == {"one.example", "two.example"}
    assert url_filter.should_crawl("https://one.example/page") is True
    assert url_filter.should_crawl("https://two.example/page") is True
    assert url_filter.should_crawl("https://three.example/page") is False


def test_exclude_patterns_have_priority_over_include_patterns() -> None:
    url_filter = URLFilter(
        include_patterns=[r"/docs/", r"/articles/"],
        exclude_patterns=[r"/private/", r"\.(?:pdf|zip)$"],
    )

    assert url_filter.should_crawl("https://example.com/docs/intro") is True
    assert url_filter.should_crawl("https://example.com/articles/async") is True
    assert url_filter.should_crawl("https://example.com/about") is False
    assert url_filter.should_crawl("https://example.com/docs/private/page") is False
    assert url_filter.should_crawl("https://example.com/docs/manual.pdf") is False


def test_empty_include_patterns_allow_every_other_valid_url() -> None:
    url_filter = URLFilter(exclude_patterns=[r"/logout(?:$|\?)"])

    assert url_filter.should_crawl("https://example.com/anything") is True
    assert url_filter.should_crawl("https://example.com/logout?next=/") is False


def test_same_domain_only_requires_allowed_domains() -> None:
    with pytest.raises(ValueError, match="allowed_domains"):
        URLFilter(same_domain_only=True)


@pytest.mark.parametrize(
    "start_urls",
    [[], ["relative/path"], ["mailto:test@example.com"]],
)
def test_from_start_urls_rejects_missing_or_invalid_urls(
    start_urls: list[str],
) -> None:
    with pytest.raises(ValueError, match="start_urls|start URL"):
        URLFilter.from_start_urls(start_urls, same_domain_only=True)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"same_domain_only": 1}, "same_domain_only"),
        ({"allowed_domains": [""]}, "allowed_domains"),
        ({"allowed_domains": ["example.com/path"]}, "allowed domain"),
        ({"include_patterns": [""]}, "include_patterns"),
        ({"exclude_patterns": ["["]}, "regular expression"),
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        URLFilter(**kwargs)
