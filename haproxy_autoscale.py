#!/usr/bin/env python

import boto3
import socket
import urllib2

from string import Template

# Boto communication

def instance_in_asg_starting_with(asg_name):
    return lambda instance: instance['AutoScalingGroupName'].startswith(asg_name)

def get_instances_by_id(instance_ids, region):
    ec2 = boto3.resource('ec2', region_name=region)
    return ec2.instances.filter(InstanceIds=instance_ids)

def get_asg_instances(asg_name, region):
    asg = boto3.client('autoscaling', region_name=region)
    all_instances = asg.describe_auto_scaling_instances()['AutoScalingInstances']
    return filter(instance_in_asg_starting_with(asg_name), all_instances)

# Wiring it together

def get_private_ips_for_asg(asg_name, region):
    # Get instances in ASG starting with given string
    all_instances = get_asg_instances(asg_name, region=region)
    operational_instances = filter(lambda i: i['LifecycleState'] == 'InService', all_instances)
    instance_ids = map(lambda i: i['InstanceId'], operational_instances)

    # Get private IPs for those instances
    instances = get_instances_by_id(instance_ids, region=region)
    ips = filter(bool, map(lambda i: i.private_ip_address, instances))

    return ips

def format_ips_as_haproxy_config_lines(ips, server_template):
    formatted_lines = [server_template % (i, ip) for (i, ip) in enumerate(ips)]
    return formatted_lines

def generate_config(tpl_source, **kwargs):
    template = Template(tpl_source)
    return template.substitute(kwargs)

AZ_METADATA_URL = 'http://169.254.169.254/latest/meta-data/placement/availability-zone'
def get_region_from_instance_meta():
    socket.setdefaulttimeout(3)
    try:
        data = urllib2.urlopen(AZ_METADATA_URL).read()
        return data[:-1]
    except urllib2.URLError:
        print >> sys.stderr, "[error] Unable to fetch instance metadata. If you are not running this on an EC2 instance, you need to supply the --region command line argument."
        sys.exit(1)

if __name__ == '__main__':
    import argparse, sys
    parser = argparse.ArgumentParser(description="Auto-scaling HAProxy configuration.")
    parser.add_argument('asgname', help='The name that the auto-scaling group starts with.')
    parser.add_argument('--region', help='The AWS region from which to fetch ASG instances. Defaults to fetching region from instance metadata.')
    parser.add_argument('-t', '--template', type=file, default='/etc/haproxy/haproxy.cfg.tpl', help='Path to haproxy template file -- replaces ${vars}.')
    parser.add_argument('-o', '--output', type=argparse.FileType('w'), default=sys.stdout, help='Path to output file. Default: stdout.')
    args = parser.parse_args()

    if args.region:
        region = args.region
    else:
        region = get_region_from_instance_meta()

    # Fetch IPs for the given auto-scaling group
    ips = get_private_ips_for_asg(args.asgname, region=region)

    # Generate haproxy `server` lines
    server_template = "server ws-server-%02d %s:8443 check port 8000"
    server_lines = format_ips_as_haproxy_config_lines(ips, server_template)

    # Interpolate the formatted `server` lines into the config template
    servers = "\n  ".join(server_lines)
    config = generate_config(args.template.read(), servers=servers)

    args.output.write(config)
    args.output.close()
