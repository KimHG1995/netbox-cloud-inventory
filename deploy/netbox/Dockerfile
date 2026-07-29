FROM netboxcommunity/netbox:v4.6.5-5.0.1

COPY deploy/netbox/plugin_requirements.txt /opt/netbox/plugin_requirements.txt
RUN uv pip install \
    --no-cache \
    --python /opt/netbox/venv/bin/python \
    -r /opt/netbox/plugin_requirements.txt
