# Jenkins for the delivery pipeline (P1-T08): the Jenkinsfile needs docker (host socket), kubectl, make,
# python, uv and git. Runs on the host network so 127.0.0.1 reaches the kind API server and the ingress.
FROM jenkins/jenkins:2.580.3-lts-jdk21@sha256:161ec15b027ea80911256552191034bf2aaede1aa39dd06ca1fef60d9b585be5
USER root
ARG DOCKER_VERSION=27.5.1
ARG DOCKER_SHA256=4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640
ARG KUBECTL_VERSION=v1.34.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends make python3 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# docker CLI (static binary, pinned sha256: Docker publishes no checksum file) and kubectl (checked against dl.k8s.io)
RUN set -eux; \
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" -o /tmp/docker.tgz; \
    echo "${DOCKER_SHA256}  /tmp/docker.tgz" | sha256sum --check --strict -; \
    tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker; rm /tmp/docker.tgz; \
    curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"; \
    echo "$(curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256")  /usr/local/bin/kubectl" | sha256sum --check --strict -; \
    chmod +x /usr/local/bin/kubectl; \
    git config --system --add safe.directory '*'
COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /uvx /usr/local/bin/
USER jenkins
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
# outside /var/jenkins_home: that is a volume, and a copy inside it would go stale on image updates
COPY casc.yaml /usr/share/jenkins/casc.yaml
ENV CASC_JENKINS_CONFIG=/usr/share/jenkins/casc.yaml \
    JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true"
