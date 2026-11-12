FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf.template
COPY docker-entrypoint.d/05-configure-nginx.sh /docker-entrypoint.d/05-configure-nginx.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/05-configure-nginx.sh \
    && chmod 755 /docker-entrypoint.d/05-configure-nginx.sh
