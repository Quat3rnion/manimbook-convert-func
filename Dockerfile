# To enable ssh & remote debugging on app service change the base image to the one below
# FROM mcr.microsoft.com/azure-functions/python:4-python3.9-appservice
FROM mcr.microsoft.com/azure-functions/python:4-python3.9

RUN apt-get update -qq \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        build-essential \
        gcc \
        cmake \
        libcairo2-dev \
        libffi-dev \
        libpango1.0-dev \
        freeglut3-dev \
        pkg-config \
        make \
        wget \
        libmagic1 \
        ghostscript 

# setup a minimal texlive installation
COPY texlive-profile.txt /tmp/
ENV PATH=/usr/local/texlive/bin/armhf-linux:/usr/local/texlive/bin/aarch64-linux:/usr/local/texlive/bin/x86_64-linux:$PATH
RUN wget --no-check-certificate -O /tmp/install-tl-unx.tar.gz http://mirror.ctan.org/systems/texlive/tlnet/install-tl-unx.tar.gz && \
    mkdir /tmp/install-tl && \
    tar -xzf /tmp/install-tl-unx.tar.gz -C /tmp/install-tl --strip-components=1 && \
    /tmp/install-tl/install-tl --profile=/tmp/texlive-profile.txt \
    && tlmgr install \
        amsmath babel-english cbfonts-fd cm-super ctex doublestroke dvisvgm everysel \
        fontspec frcursive fundus-calligra gnu-freefont jknapltx latex-bin \
        mathastext microtype ms physics preview ragged2e relsize rsfs \
        setspace standalone tipa wasy wasysym xcolor xetex xkeyval

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

COPY requirements.txt /
RUN pip install -r /requirements.txt

COPY . /home/site/wwwroot