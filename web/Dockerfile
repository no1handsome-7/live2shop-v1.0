FROM node:22-alpine AS build
WORKDIR /web
COPY package.json .
RUN npm install
COPY . .
ARG VITE_GATEWAY_URL
ARG VITE_REC_URL
ARG VITE_HLS_URL
ENV VITE_GATEWAY_URL=$VITE_GATEWAY_URL
ENV VITE_REC_URL=$VITE_REC_URL
ENV VITE_HLS_URL=$VITE_HLS_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
