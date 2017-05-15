import re
from setuptools import setup

from wfuzz.facade import version
 
 
with open("README.md", "rb") as f:
    long_descr = f.read().decode("utf-8")
 
 
setup(
    name = "wfuzz",
    packages = ["wfuzz"],
    entry_points = {
        "console_scripts": [
        },


    entry_points={
        'console_scripts': [
            'wfuzz = wfuzz.wfuzz:main',
            'wfpayload = wfuzz.wfuzz:main_filter',
        ],
    }
    version = version,
    description = "Wfuzz - The web fuzzer",
    long_description = long_descr,
    author = "Xavi Mendez (@x4vi_mendez)",
    url = "http://wfuzz.org",
    )
