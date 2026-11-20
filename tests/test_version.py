from version import APP_VERSION


def test_version_api_uses_single_canonical_version():
    import app

    result = app.api_version()
    assert result["application"] == "ChartAnalyzer"
    assert result["version"] == APP_VERSION == "7.3.0"
    assert result["version_source"] == "version.py"


def test_research_provenance_uses_same_canonical_version():
    import research_provenance

    assert research_provenance.APP_VERSION == APP_VERSION
