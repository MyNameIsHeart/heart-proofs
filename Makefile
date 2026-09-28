.PHONY: convert pdf build serve clean

convert:
	python3 scripts/convert.py

pdf:
	python3 scripts/convert.py --pdf

build: pdf
	hugo --minify

serve: convert
	hugo server -D --disableFastRender

clean:
	python3 scripts/convert.py --clean
	rm -rf public resources/_gen
