# Taylor: OpenStack Object Storage Easy Manipulator

## Abstract
WebApp for OpenStack Object Storage, implemented as WSGI middleware.

## How to use
1. Access to OpenStack Object Storage Server by Web browser.
   * If storage URL was 'http://storage.example.tld:8080/v1/AUTH_admin', Connect to 'http://storage.example.tld:8080/taylor'.
2. Input username and password in login form.
3. Use.

## Features
by Web browser...
* viewing container list
* viewing container status
* creating container
* deleting container
* setting/deleting/updating container metadata
* setting ACL
* setting/unsetting version-storing container
* viewing object list
* viewing object status
* uploading object
* deleting object
* retrieving object
* setting/deleting/updating object metadata
* copying object to other container
* setting/unsetting a schedule to delete
* enable to use multibyte for container, object, and their metadata.
* pseudo directory support (but can't upload objects with pseudo directory, maybe)
* adding container-sync metadate

## Setting
in proxy-server.conf:
```
[pipeline:main]
pipeline = catch_errors proxy-logging healthcheck cache taylor tempauth proxy-logging proxy-server

[filter:taylor]
use = egg:Taylor#taylor
page_path = /taylor
auth_url = http://localhost:8080/auth/v1.0
auth_version = 1
items_per_page = 5
cookie_max_age = 3600
enable_versions = no
enable_object_expire = no
enable_container_sync = no
```

### setting items
* page_path
  * setting of base path of this application.
* auth_url
  * auth URL of OpenStack Storage.
* auth_version
  * authorization version: 1: tempauth, 2: keystone
* items_per_page
  * showing item count per a page. default: 5
* cookie_max_age
  * max age time of cookie. default: 3600(sec.)
* enable_versions
  * enable setting a version-storing container. default: no
* enable_object_expire
  * enable setting a schedule for expiring objects. default: no
* enable_container_sync
  * enable adding container-sync metadata. default: no


## How I Learned to Stop Worrying
I don't care Internet Explorer.

checking by Google Chrome ver 26.0.

## Required
* swift-1.8.0(grizzly)
  * using swob. not using WebOb.
* mako
* python-swiftclient

## Version
0.1(2013-08-13)

## URL
https://github.com/yuzawat/Taylor

## Author
yuzawat \<suzdalenator at gmail.com\>
