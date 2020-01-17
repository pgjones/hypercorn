import os
import sys

from setuptools import setup, find_packages

if sys.version_info < (3, 7):
    sys.exit("Python 3.7 is the minimum required version")

PROJECT_ROOT = os.path.dirname(__file__)

about = {}
with open(os.path.join(PROJECT_ROOT, "src", "hypercorn", "__about__.py")) as file_:
    exec(file_.read(), about)

with open(os.path.join(PROJECT_ROOT, "README.rst")) as file_:
    long_description = file_.read()

INSTALL_REQUIRES = [
    "h11",
    "h2 >= 3.1.0",
    "priority",
    "toml",
    "typing_extensions",
    "wsproto >= 0.14.0",
]

TESTS_REQUIRE = [
    "asynctest",
    "hypothesis",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "pytest-trio",
    "trio",
]

setup(
    name="Hypercorn",
    version=about["__version__"],
    python_requires=">=3.7",
    description="A ASGI Server based on Hyper libraries and inspired by Gunicorn.",
    long_description=long_description,
    url="https://gitlab.com/pgjones/hypercorn/",
    author="P G Jones",
    author_email="philip.graham.jones@googlemail.com",
    license="MIT",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    packages=find_packages("src"),
    package_dir={"": "src"},
    py_modules=["hypercorn"],
    install_requires=INSTALL_REQUIRES,
    extras_require={
        "h3": ["aioquic >= 0.8.1, < 1.0"],
        "tests": TESTS_REQUIRE,
        "trio": ["trio >= 0.11.0"],
        "uvloop": ["uvloop"],
    },
    tests_require="hypercorn[tests]",
    entry_points={"console_scripts": ["hypercorn = hypercorn.__main__:main"]},
    include_package_data=True,
)
