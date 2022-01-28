r""" Continuous integration by performing tests, coverage, formatting, and package building.

1. if this script runs locally to success, the CI process should also run successfully
2. continuous_integration.py executes automatically in an Azure DevOps Pipeline on push
3. CI results can be viewed after a push at: #TODO add link
4. if CI succeeds in the Azure DevOps pipeline, submit a pull request to complete the continuous deployment build and release

Examples
--------
#### default CI process
python continuous_integration.py
#### using command line arguments specified in conftest.py options
python continuous_integration.py --server=localhost\SQLEXPRESS

See Also
--------
CONTRIBUTING.md CICD Build Pipelines for a general overview of the CICD process
conftest.py options variable for command line arguments allowed
setup.cfg for CICD associated settings
continuous_integration.yml for Azure DevOps Pipeline CI definition
continuous_deployment.py and continuous_deployment.yml for continuous deployment
"""
import os
import subprocess
import configparser
import argparse
import glob

from conftest import options


def run_cmd(cmd):
    """Generic command line process and error if needed."""
    # run all commands in virtual environment
    cmd[0] = os.path.join(os.getcwd(), "env", "Scripts", cmd[0])
    # call command line process
    status = subprocess.run(cmd, capture_output=True)
    if status.returncode != 0:
        # status = subprocess.call(cmd, shell=True)
        if len(status.stderr) > 0:
            msg = status.stderr.decode("utf-8")
        else:
            msg = status.stdout.decode("utf-8")
        raise RuntimeError(msg)


def run_black(config):
    """black to auto-format code to standard."""
    print(f"running black for module: {config['metadata']['name']}")
    run_cmd(["black", config["metadata"]["name"]])


def run_flake8(config):
    """flake8 to lint and check code quality"""
    try:
        run_cmd(
            [
                "flake8",
                config["metadata"]["name"],
                f"--output-file={config['flake8']['output-file']}",
            ]
        )
        print(f"generated flake8 statistics file: {config['flake8']['output-file']}")
    except RuntimeError:
        print(f"see file for flake8 errors: {config['flake8']['output-file']}")
        raise


def support_file_black_flake8():
    """additionally run black and flake8 for support files."""
    for cmd in ["black", "flake8"]:
        for fp in [
            "tests/",
            "conftest.py",
            "continuous_integration.py",
        ]:
            print(f"running {cmd} for {fp}")
            run_cmd([cmd, fp])


def run_coverage_pytest(config, args):
    """pytest and coverage to ensure code works as desired and is covered by tests. Also produces test xml report for genbadge."""
    print(f"running coverage for module: {config['metadata']['name']}")
    print(f"running tests for directory: {config['tool:pytest']['testpaths']}")
    # required arguments
    cmd = [
        "coverage",
        "run",
        "--branch",
        "-m",
        f"--source={config['metadata']['name']}",
        "pytest",
        f"--junitxml={config['user:pytest']['junitxml']}",
        "-v",
    ]
    # add optional arguments defined by conftest.py options
    cmd += ["--" + k + "=" + v for k, v in args.items()]

    # use coverage to call pytest
    run_cmd(cmd)
    print(f"generated coverage sqlite file: {config['coverage:run']['data_file']}")
    print(f"generated test xml file: {config['user:pytest']['junitxml']}")


def coverage_html(config):
    """coverage html report for user viewing"""
    print(
        f"generating coverage html file: {os.path.join(config['coverage:html']['directory'], 'index.html')}"
    )
    run_cmd(["coverage", "html"])


def coverage_xml(config):
    """coverage xml report for genbadge"""
    print(f"generating coverage xml file: {config['coverage:xml']['output']}")
    run_cmd(["coverage", "xml"])


def generage_badges(config):
    """generate badges using genbadge"""
    badges = {
        "tests": config["user:pytest"]["junitxml"],
        "coverage": config["coverage:xml"]["output"],
        "flake8": config["flake8"]["output-file"],
    }
    for b, i in badges.items():
        fp = f"{config['genbadge']['output']}{b}.svg"
        print(f"generating badge for {b} at: {fp}")
        run_cmd(["genbadge", b, "-i", i, "-o", fp])


def build_package():
    "build Python package for upload to PyPi.org"

    outdir = os.path.join(os.getcwd(), "dist")
    print(f"building package in directory: {outdir}")

    # build package .gz and .whl files
    run_cmd(["python", "-m", "build", f"--outdir={outdir}"])
    print(f"built source archive {glob.glob(os.path.join(outdir,'*.tar.gz'))}")
    print(f"built distribution {glob.glob(os.path.join(outdir,'*.whl'))}")

    # check build result
    run_cmd(["twine", "check", os.path.join(outdir, "*")])


# parameters from setup.cfg
config = configparser.ConfigParser()
config.read("setup.cfg")

# command line arguments from confest options since both pytest and argparse use the same parameters
parser = argparse.ArgumentParser()
for opt in options:
    parser.add_argument(opt, **options[opt])
args = parser.parse_args()

# convert args to dictionary to allow to be used as command line args
args = vars(args)
# ignore None as would be passed as "None"
args = {k: v for k, v in args.items() if v is not None}

run_black(config)
run_flake8(config)
support_file_black_flake8()
run_coverage_pytest(config, args)
coverage_html(config)
coverage_xml(config)
generage_badges(config)
build_package()
