.PHONY: convert build serve clean

convert:
	python3 scripts/convert.py

build: convert
	hugo --minify

serve: convert
	hugo server -D --disableFastRender

clean:
	python3 scripts/convert.py --clean
	rm -rf public resources/_gen
