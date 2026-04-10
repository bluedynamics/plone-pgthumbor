# Configuration file for the Sphinx documentation builder.

# -- Project information -----------------------------------------------------

project = "plone.pgthumbor"
copyright = "2026, BlueDynamics Alliance"  # noqa: A001
author = "Jens Klein and contributors"
release = "0.6.2"

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",
    "sphinxcontrib.mermaid",
    "sphinx_design",
    "sphinx_copybutton",
]

myst_enable_extensions = [
    "deflist",
    "colon_fence",
    "fieldlist",
]

myst_fence_as_directive = ["mermaid"]

templates_path = ["_templates"]
exclude_patterns = []

# mermaid options
mermaid_output_format = "raw"

# -- Options for HTML output -------------------------------------------------

html_theme = "shibuya"

html_theme_options = {
    "logo_target": "/plone-pgthumbor/",
    "accent_color": "violet",
    "color_mode": "dark",
    "dark_code": True,
    "nav_links": [
        {
            "title": "Ecosystem",
            "url": "https://bluedynamics.github.io/zodb-pgjsonb/ecosystem.html",
            "children": [
                {
                    "title": "Dashboard",
                    "url": "https://bluedynamics.github.io/zodb-pgjsonb/ecosystem.html",
                    "summary": "Overview of all packages",
                },
                {
                    "title": "zodb-pgjsonb",
                    "url": "https://bluedynamics.github.io/zodb-pgjsonb/",
                    "summary": "PostgreSQL JSONB storage",
                },
                {
                    "title": "zodb-json-codec",
                    "url": "https://bluedynamics.github.io/zodb-json-codec/",
                    "summary": "Rust pickle↔JSON transcoder",
                },
                {
                    "title": "plone-pgcatalog",
                    "url": "https://bluedynamics.github.io/plone-pgcatalog/",
                    "summary": "PostgreSQL-backed catalog",
                },
                {
                    "title": "plone-pgthumbor",
                    "url": "https://bluedynamics.github.io/plone-pgthumbor/",
                    "summary": "Thumbor image scaling",
                },
            ],
        },
        {
            "title": "GitHub (addon)",
            "url": "https://github.com/bluedynamics/plone-pgthumbor",
        },
        {
            "title": "PyPI (addon)",
            "url": "https://pypi.org/project/plone.pgthumbor/",
        },
        {
            "title": "GitHub (loader)",
            "url": "https://github.com/bluedynamics/zodb-pgjsonb-thumborblobloader",
        },
        {
            "title": "PyPI (loader)",
            "url": "https://pypi.org/project/zodb-pgjsonb-thumborblobloader/",
        },
    ],
}

html_extra_path = ["llms.txt"]
html_static_path = ["_static"]
html_logo = "_static/logo-web.png"
html_favicon = "_static/favicon.ico"
