# Configuration file for the Sphinx documentation builder.

# -- Project information -----------------------------------------------------

project = "plone.pgthumbor"
copyright = "2024-2026, BlueDynamics Alliance"  # noqa: A001
author = "Jens Klein and contributors"
release = "1.0"

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
            "title": "GitHub (Plone addon)",
            "url": "https://github.com/bluedynamics/plone-pgthumbor",
        },
        {
            "title": "GitHub (Thumbor loader)",
            "url": "https://github.com/bluedynamics/zodb-pgjsonb-thumborblobloader",
        },
        {
            "title": "PyPI",
            "url": "https://pypi.org/project/plone.pgthumbor/",
        },
    ],
}

html_extra_path = ["llms.txt"]
html_static_path = ["_static"]
html_favicon = "_static/favicon.ico"
