SRC = cached_path.py

.PHONY : version
version :
	@python -c 'from allennlp.version import VERSION; print(f"AllenNLP v{VERSION}")'

.PHONY : check-for-cuda
check-for-cuda :
	@python -c 'import torch; assert torch.cuda.is_available(); print("Cuda is available")'

#
# Testing helpers.
#

.PHONY : flake8
flake8 :
	flake8 cached_path.py tests

.PHONY : format
format :
	black --check cached_path.py tests

.PHONY : typecheck
typecheck :
	mypy cached_path.py tests --cache-dir=/dev/null

.PHONY : test
test :
	pytest --color=yes -v -rf --durations=40 \
			--cov-config=.coveragerc \
			--cov=$(SRC) \
			--cov-report=xml

#
# Setup helpers
#

.PHONY : install
install :
	# Ensure pip, setuptools, and wheel are up-to-date.
	pip install --upgrade pip setuptools wheel
	# Due to a weird thing with pip, we may need egg-info before running `pip install -e`.
	# See https://github.com/pypa/pip/issues/4537.
	# python setup.py install_egg_info
	pip install --upgrade --upgrade-strategy eager -e . -r dev-requirements.txt

.PHONY : clean
clean :
	rm -rf .pytest_cache/
	rm -rf *.egg-info/
	rm -rf dist/
	rm -rf build/
	find . | grep -E '(\.mypy_cache|__pycache__|\.pyc|\.pyo$$)' | xargs rm -rf
