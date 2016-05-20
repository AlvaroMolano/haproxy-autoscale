HAProxy auto-scale
===

This is a small script that grabs the private IPs of the instances
in an AWS auto-scaling group, and outputs HAProxy config based on a
template.

Usage
---

Typically, you'll want this script running as a cron job on your HAProxy
instances, regenerating fresh HAProxy config every minute or so,
depending on your needs.

```bash
$ ./haproxy_autoscale.py --help
usage: haproxy_autoscale.py [-h] [-t TEMPLATE] [-o OUTPUT] asgname

Auto-scaling HAProxy configuration.

positional arguments:
  asgname               The name that the auto-scaling group starts with.

optional arguments:
  -h, --help            show this help message and exit
  --region REGION       The AWS region from which to fetch ASG instances.
                        Defaults to fetching region from instance metadata.
  -t TEMPLATE, --template TEMPLATE
                        Path to haproxy template file -- replaces ${vars}.
  -o OUTPUT, --output OUTPUT
                        Path to output file. Default: stdout.
```

An example run, printing to stdout:

```bash
$ python haproxy_autoscale.py -t ./haproxy.cfg.tpl app-server
```

Dependencies
---

* [boto3](http://boto3.readthedocs.io/en/latest/index.html)

Notes
---

AWS auto-scaling groups have notification events that you can use to
instantly update the config whenever a group scales up or down.
