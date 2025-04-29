so turns out poetry is a bitch and im not familiar with it

asaik its a packagae manager. 

1. obviously dont use your own env it sets up it own.
2. using toml as single aource of truth for dependency resolution

3. once toml populated with imports and boilerplate necessities can run poetry install

4. obviously when invoking scripts in the repo with poetry env do it from root of project (where pyproject.toml manifest is) 
`poetry run python -m funding_curve.tests.smoke_binance`

```
.
├── README.md
├── funding_curve
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-313.pyc
│   │   └── funding_collectors.cpython-313.pyc
│   ├── builders
│   │   ├── __init__.py
│   │   └── curve.py
│   ├── collectors
│   │   └── __init__.py
│   ├── config.py
│   ├── funding_collectors.py
│   ├── pipelines
│   │   ├── __init__.py
│   │   ├── ingest.py
│   │   └── snapshot.py
│   ├── storage
│   │   ├── __init__.py
│   │   ├── db.py
│   │   └── schemas.py
│   ├── tests
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   │   ├── __init__.cpython-313.pyc
│   │   │   └── smoke_binance.cpython-313.pyc
│   │   ├── smoke_binance.py
│   │   └── test_builders.py
│   └── utils
│       ├── __init__.py
│       └── time.py
├── myenv
│   ├── bin
│   │   ├── Activate.ps1
│   │   ├── activate
│   │   ├── activate.csh
│   │   ├── activate.fish
│   │   ├── pip
│   │   ├── pip3
│   │   ├── pip3.9
│   │   ├── python -> python3
│   │   ├── python3 -> /Library/Developer/CommandLineTools/usr/bin/python3
│   │   └── python3.9 -> python3
│   ├── include
│   ├── lib
│   │   └── python3.9
│   │       └── site-packages
│   └── pyvenv.cfg
├── poetry.lock
└── pyproject.toml
```
