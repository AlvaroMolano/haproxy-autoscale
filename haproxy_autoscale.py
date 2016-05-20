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

def mark_instance_as_unhealthy(instance_id, region):
    asg = boto3.client('autoscaling', region_name=region)
    asg.set_instance_health(InstanceId=instance_id, HealthStatus="Unhealthy", ShouldRespectGracePeriod=True)

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
    return filter(bool, map(lambda i: (i.id, i.private_ip_address), instances))

def format_instances_as_haproxy_config_lines(instances, server_template):
    formatted_lines = [server_template % (i, ip) for (i, ip) in instances]
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

def write_config(instances, template_file, output_file):
    # Generate haproxy `server` lines
    server_template = "server %s %s:8443 check port 8000"
    server_lines = format_instances_as_haproxy_config_lines(instances, server_template)

    # Interpolate the formatted `server` lines into the config template
    servers = "\n  ".join(server_lines)
    config = generate_config(template_file.read(), servers=servers)

    output_file.write(config)
    output_file.close()


HAP_BUFSIZE = 8192
class HASocket:
    def __init__(self, socket_filename):
        self.socket_filename = socket_filename

    def __enter__(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        self.socket.connect(self.socket_filename)
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.socket.close()

    def call(self, cmd):
        # Send the command
        self.socket.sendall(cmd + "\r\n")

        # Receive the response
        output = ""
        while True:
            response = self.socket.recv(HAP_BUFSIZE)
            if not response:
                break

            output += response.decode('ASCII')

        return output

def get_instance_id_and_health(server_state_line):
    server_state = server_state_line.strip().split(" ")
    return (server_state[3], int(server_state[5]) != 0)

def get_unhealthy_instance_ids(servers_state):
    servers = filter(bool, servers_state.split("\n"))
    instance_id_and_health_tuples = map(get_instance_id_and_health, servers)
    unhealthy_instances = filter(lambda (server_id, healthy): not healthy, instance_id_and_health_tuples)
    unhealthy_instance_ids = map(lambda (name, _): name, unhealthy_instances)
    return unhealthy_instance_ids

def get_unhealthy_instance_ids_from_haproxy_socket(socket_filename):
    # Get health for each current haproxy server instance
    with HASocket(socket_filename) as ha:
        servers_state = ha.call('show servers state servers')
        return get_unhealthy_instance_ids(servers_state)

def mark_instances_as_unhealthy(instance_ids, region):
    for instance_id in instance_ids:
        mark_instance_as_unhealthy(instance_id, region)

if __name__ == '__main__':
    import argparse, sys, os
    parser = argparse.ArgumentParser(description="Auto-scaling HAProxy configuration.")
    parser.add_argument('asgname', help='The name that the auto-scaling group starts with.')
    parser.add_argument('--region', help='The AWS region from which to fetch ASG instances. Defaults to fetching region from instance metadata.')
    parser.add_argument('-t', '--template', type=file, default='/etc/haproxy/haproxy.cfg.tpl', help='Path to haproxy template file -- replaces ${vars}.')
    parser.add_argument('-o', '--output', type=argparse.FileType('w'), default=sys.stdout, help='Path to output file. Default: stdout.')
    parser.add_argument('-s', '--socket', default='/run/haproxy/admin.sock', help='Path to HAProxy admin socket.')
    args = parser.parse_args()

    if args.region:
        region = args.region
    else:
        region = get_region_from_instance_meta()

    # Fetch IPs for the given auto-scaling group and write it to file
    instances = get_private_ips_for_asg(args.asgname, region=region)

    # Get unhealthy instance ids from HAProxy admin socket
    unhealthy_instance_ids = get_unhealthy_instance_ids_from_haproxy_socket(args.socket)
    mark_instances_as_unhealthy(unhealthy_instance_ids, region)

    # Actually write out the new config if everything went ok
    write_config(instances, args.template, args.output)

