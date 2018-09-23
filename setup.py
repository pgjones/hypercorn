import os
import sys

from setuptools import setup, find_packages

if sys.version_info < (3,6,1):
    sys.exit('Python 3.6.1 is the minimum required version')

PROJECT_ROOT = os.path.dirname(__file__)

about = {}
with open(os.path.join(PROJECT_ROOT, 'hypercorn', '__about__.py')) as file_:
    exec(file_.read(), about)

with open(os.path.join(PROJECT_ROOT, 'README.rst')) as file_:
    long_description = file_.read()

INSTALL_REQUIRES = [
    'h11',
    'h2',
    'pytoml',
    'typing_extensions',
    'wsproto >= 0.12.0',
]

setup(
    name='Hypercorn',
    version=about['__version__'],
    python_requires='>=3.6.1',
    description="A ASGI Server based on Hyper libraries and inspired by Gunicorn.",
    long_description=long_description,
    url='https://gitlab.com/pgjones/hypercorn/',
    author='P G Jones',
    author_email='philip.graham.jones@googlemail.com',
    license='MIT',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    packages=find_packages(exclude=["tests", "tests.*"]),
    py_modules=['hypercorn'],
    install_requires=INSTALL_REQUIRES,
    extras_require={
        'uvloop': ['uvloop'],
    },
    tests_require=INSTALL_REQUIRES + [
        'hypothesis',
        'pytest',
        'pytest-asyncio',
    ],
    entry_points={
        'console_scripts': [
            'hypercorn = hypercorn.__main__:main',
        ],
    },
)
