from __future__ import print_function
import os
import sys
import yaml
import json
from botocore.client import Config
import boto3
import re
import random

class aws_inventory(object):
  def __init__(self, config):
    # Initialize an empty inventory
    self.inventory = {}
    self.inventory['_meta'] = {'hostvars': {}}
    self.inventory['all'] = {'hosts': [], 'vars': {}}

    # Add localhost to inventory
    self.inventory['all']['hosts'].append('localhost')
    self.inventory['_meta']['hostvars']['localhost'] = {}
    self.inventory['_meta']['hostvars']['localhost']['ansible_host'] = 'localhost'
    self.inventory['_meta']['hostvars']['localhost']['ec2_public_dns_name'] = 'localhost'
    self.inventory['_meta']['hostvars']['localhost']['ec2_public_ip_address'] = '127.0.0.1'
    self.inventory['_meta']['hostvars']['localhost']['ec2_private_ip_address'] = '127.0.0.1'
    self.inventory['_meta']['hostvars']['localhost']['ansible_connection'] = 'local'

    # Read in the config to construct and build host groups. Autodetect if it's a file or string.
    if os.path.isfile(config):
      self.config = yaml.load(open(config, 'r'), Loader=yaml.FullLoader)
    elif type(config) == str:
      self.config = yaml.load(config, Loader=yaml.FullLoader)
    else:
      raise TypeError

    # Set some config defaults, if not present
    if not 'hostnames' in self.config: self.config['hostnames'] = {}
    if not 'source' in self.config['hostnames']: self.config['hostnames']['source'] = 'ec2_tag'
    if self.config['hostnames']['source'] == 'ec2_tag' and not 'ec2_tag' in self.config['hostnames']:
      self.config['hostnames']['var'] = 'Name'
    if self.config['hostnames']['source'] == 'ec2_metadata' and not 'ec2_metadata' in self.config['hostnames']:
      self.config['hostnames']['var'] = 'PublicDnsName'
    # Set some boto3 config defaults
    if not 'region_name' in self.config['boto3']: self.config['boto3']['region_name'] = 'us-east-1'
    if not 'connect_timeout' in self.config['boto3']: self.config['boto3']['connect_timeout'] = 5
    if not 'read_timeout' in self.config['boto3']: self.config['boto3']['read_timeout'] = 20
    if not 'max_attempts' in self.config['boto3']: self.config['boto3']['max_attempts'] = 10

    # Create empty host groups from the config
    for g in self.config['groups']:
      self.inventory[g['name']] = []

    config = Config(region_name = self.config['boto3']['region_name'],
                    connect_timeout = self.config['boto3']['connect_timeout'],
                    read_timeout = self.config['boto3']['read_timeout'],
                    retries = {'max_attempts': self.config['boto3']['max_attempts']})

    self.ec2 = boto3.client('ec2', config=config,
                            aws_access_key_id = self.config['boto3']['aws_access_key_id'],
                            aws_secret_access_key = self.config['boto3']['aws_secret_access_key'])
    self.rds = boto3.client('rds', config=config,
                            aws_access_key_id = self.config['boto3']['aws_access_key_id'],
                            aws_secret_access_key = self.config['boto3']['aws_secret_access_key'])

  # For sorting
  def alphanum_key(self, s):
    '''http://nedbatchelder.com/blog/200712/human_sorting.html'''
    tryint = lambda s: int(s) if s.isdigit() else s
    return [ tryint(c) for c in re.split('(\d+)', s) ]

  # Formats: json, raw (a raw Python dict)
  def run(self, format='json'):
    # Get EC2 instance data
    aws_resp = self.ec2.describe_instances()
    if aws_resp['ResponseMetadata']['HTTPStatusCode'] != 200:
      print("ERROR: Received HTTP status code {} from AWS. Exiting.".format(aws_resp['ResponseMetadata']['HTTPStatusCode']), file=sys.stderr)
      exit(1)
    # Loop through the AWS response and add the relevant instance info to the inventory
    for item in aws_resp.items():
      #print("{}\n\n".format(dir(item)))
      for i in item[1]:
        # For some reason every ec2 instance is listed inside a dict, under the key "Instances"
        if type(i) == dict:
          for m in i['Instances']:
            hostname = ''
            hostvars = []
            tags = {}
            #print("{}".format(m))
            # If it is not running, skip it
            if m['State']['Name'] != 'running': continue
            if self.config['hostnames']['source'] == 'ec2_tag':
              # If the ec2 tag matches what we use to assign inventory hostnames, use the value as the hostname
              if 'Tags' in m:
                found = False
                for t in m['Tags']:
                  if t['Key'] == self.config['hostnames']['var']:
                    hostname = t['Value']
                    found = True
                    break
                if not found:
                  print("WARNING: Instance {} has no tag \"{}\". Skipping.".format(m['InstanceId'], self.config['hostnames']['var']), file=sys.stderr)
                  continue
              # If instance has no ec2 tags, skip it.
              else:
                print("WARNING: Instance {} has no tags. Skipping.".format(m['InstanceId']), file=sys.stderr)
                continue
            # If the ec2 metadata variable matches what we use to assign inventory hostnames, use the value as the hostname
            if self.config['hostnames']['source'] == 'ec2_metadata' and self.config['hostnames']['var'] in m:
              hostname = m[self.config['hostnames']['var']]
            # Prep host's ec2 tags for inclusion in inventory
            for t in m['Tags']:
              tags['ec2_tag_%s' % t['Key'].replace(':', '_')] = t['Value']
            # Add host to group 'all'
            self.inventory['all']['hosts'].append(hostname)
            # Add any hostvars for the host to the inventory
            self.inventory['_meta']['hostvars'][hostname] = {}
            self.inventory['_meta']['hostvars'][hostname].update(tags)
            if 'hostvars' in self.config and type(self.config['hostvars']) == dict:
              for h in self.config['hostvars']:
                if re.search(h, hostname):
                  self.inventory['_meta']['hostvars'][hostname].update(self.config['hostvars'][h])
            if 'PublicDnsName' in m.keys():
              # The hostvar "ansible_host" is what ansible uses when connecting to the host
              self.inventory['_meta']['hostvars'][hostname]['ansible_host'] = m['PublicDnsName']
              self.inventory['_meta']['hostvars'][hostname]['ec2_public_dns_name'] = m['PublicDnsName']
            else:
              print("ERROR: no PublicDnsName for host -- {}. Aborting.".format(m), file=sys.stderr)
              exit(1)
            if 'PublicIpAddress' in m.keys():
              # The hostvar "ansible_host" is what ansible uses when connecting to the host
              #self.inventory['_meta']['hostvars'][hostname]['ansible_host'] = m['PublicIpAddress']
              self.inventory['_meta']['hostvars'][hostname]['ec2_public_ip_address'] = m['PublicIpAddress']
            else:
              print("WARNING: no PublicIpAddress for host -- {}".format(m), file=sys.stderr)
            self.inventory['_meta']['hostvars'][hostname]['ec2_private_ip_address'] = m['PrivateIpAddress']

    # TODO: Get relevant RDS instance data and add it to the inventory
    #for item in self.rds.describe_db_instances().items():
    #  #print("{}\n\n".format(dir(item)))
    #  for i in item[1]:
    #    print(item)


    # TODO: Get relevant ElastiCache instance data and add it to the inventory


    # Iterate through each host group, adding hosts from "all" that match
    for g in self.config['groups']:
      self.inventory[g['name']] = {'hosts': [], 'vars': {}}
      if 'vars' in g: self.inventory[g['name']]['vars'].update(g['vars'])
      for h in self.inventory['all']['hosts']:
        # Test whether the metadata hostvar we group by is in the host's metadata and that its value matches
        if g['hostvar'] in self.inventory['_meta']['hostvars'][h] and re.search(g['match'], self.inventory['_meta']['hostvars'][h][g['hostvar']]):
          self.inventory[g['name']]['hosts'].append(h)

    # Per group, shuffle host order if specified
    for group in self.config['groups']:
      if 'order' in group:
        if group['order'].lower() == 'shuffle':
          random.shuffle(self.inventory[group['name']]['hosts'])
        elif group['order'].lower() == 'sorted':
          self.inventory[group['name']]['hosts'].sort(key=self.alphanum_key)

    # Return the inventory for outputting
    if format == 'json':
      return json.dumps(self.inventory, sort_keys=True, indent=2, separators=(',', ': '))
    elif format == 'raw':
      return self.inventory


