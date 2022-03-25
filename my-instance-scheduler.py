#!/usr/bin/python3
#  python /home/ec2-user/ManageCloudVMsss.py -a stop -p "aws:ca-central-1"
# python3 /home/ec2-user/ManageCloudVMss3.py -a stop -p "aws:ca-central-1,aws:eu-central-1,azure:Ebi-test-delete-rg"
'''
A script to stop or start all VM instances in a given cloud platform region
(AWS) or resource group (Azure).
'''

import os
import argparse
import re
import boto3
import time
from msrestazure.azure_exceptions import CloudError
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.identity import EnvironmentCredential

# Configuration Variables
azure_credentials_file = os.environ['HOME'] + '/azure_credentials_file'
from_address = 'doebi.alale@ehi.com'
mailto = 'doebi.alale@ehi.com'
smtp_relay = 'smtp.corp.erac.com'
targets = {}
verbose = False

# Functions

def exit_error(message):
    print("Exited gracefully due to errors")
    
def manage_aws_hosts(operation, region):
    '''
    Given an operation and an AWS region, loop through all of the VM
    instances in that region and perform the specified operation.
    '''
    # Connect to AWS and pull the list of instances
    ec2 = boto3.client('ec2', region_name=region)    
    response = ec2.describe_instances() 

    # For each instance found, determine the instance IP and instance ID
    # Then, perform the appropriate operation
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:

            # Skip instances that are terminated
            instance_state = instance['State']['Name']
            if instance_state != 'terminated':
                instance_id = instance['InstanceId']
                # tag = ec2.describe_tags()
                # print(tag)
                instance_ip = instance['NetworkInterfaces'][0]['PrivateIpAddress']

                if operation == 'stop':
                    if verbose:
                        print ('Stopping ' + instance_ip)                        
                    try:
                        ec2.stop_instances(InstanceIds=[instance_id],
                                            DryRun=False)
                    except ClientError as e:
                        exit_error(e.message)
              
                if operation == 'start':
                    if verbose:
                        print ('Starting ' + instance_ip)
                    try:
                        ec2.start_instances(InstanceIds=[instance_id],
                                            DryRun=False)
                    except ClientError as e:
                        exit_error(e.message)

def manage_azure_hosts(operation, resgroup):
    '''
    Given an operation and an Azure resource group, loop through all of the
    VM instances in that resource group and perform the specified operation.
    '''

    # Obtain the credentials from the specified credentials file and map
    # them to variables, then create a credentials object
        
    credentials = EnvironmentCredential()
    subscription_id=os.environ['AZURE_SUBSCRIPTION_ID']
    client_id=os.environ['AZURE_CLIENT_ID']
    secret=os.environ['AZURE_CLIENT_SECRET']
    tenant=os.environ['AZURE_TENANT_ID']

    # Create Azure resource group, compute, and network clients to pull
    # information from Azure

    resource_group_client = ResourceManagementClient(credentials, subscription_id)
    compute_client = ComputeManagementClient(credentials, subscription_id)
    network_client = NetworkManagementClient(credentials, subscription_id)

    # Get a list of all VM instances in the resource group. For each
    # one, determine the primary IP and VM name. Then, perform the
    # specified operation
    try:
        azure_vms = compute_client.virtual_machines.list(resgroup)  
        for vm in azure_vms:
            nic_id = vm.network_profile.network_interfaces[0].id
            nic_id = nic_id.split('/')[-1]
            nic = network_client.network_interfaces.get(resgroup, nic_id)
            primary_ip = nic.ip_configurations[0].private_ip_address
            vm_name = vm.name

    #         # We do NOT want to shut down the Terraform/Ansible server as
    #         # that's where the automation runs so the scheduled task to power
    #         # back on would never run
            if re.match(r'^((?!tfa).)*$', vm_name):
                if operation == 'stop':
                    if verbose:
                        print ('Stopping ' + vm_name)
                    vmstatus = compute_client.virtual_machines.instance_view(resgroup, vm_name)
                    # Don't shut down VMs that aren't running
                    if vmstatus.statuses[1].display_status == 'VM running':
                        compute_client.virtual_machines.begin_power_off(resgroup, vm_name)
                   
                    # In Azure, shutting down a host doesn't deallocate it so
                    # we are still charged. Wait for the shutdown to complete,
                    # then deallocate the VM
                    if vmstatus.statuses[1].display_status != 'VM deallocated':
                        vmrunning = True
                        while vmrunning is True:
                            vmstatus = compute_client.virtual_machines.instance_view(resgroup, vm_name)
                            if vmstatus.statuses[1].display_status == "VM stopped":
                                vmrunning = False
                            else:
                                time.sleep(15)
                        compute_client.virtual_machines.begin_deallocate(resgroup,
                                                                    vm_name)

                if operation == 'start':
                    if verbose:
                        print ('Starting ' + vm_name)
                    compute_client.virtual_machines.begin_start(resgroup,
                                                            vm_name)
    except CloudError as e:
        exit_error(e.message)

if __name__ == '__main__':
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Stop or start VM ' +
                                     'instances in the specfied cloud ' +
                                     'environment(s).',
                                     epilog=
'''
Cloud platforms should be specified in the following format:

AWS:
    aws:<region>

Azure:
    azure:<resource_group>

Multiple platforms can be specified in a comma separated list. For example:

    --platforms='aws:us-east-1,azure:poc-rg'
''', formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-a', '--action', dest='action', default='stop',
                        help='Action to perform: "stop" or "start"')
    parser.add_argument('-p', '--platforms', dest='platforms', default='aws:ca-central-1',
                        help='Cloud platforms in which to perform the ' +
                        'specified action.')
    parser.add_argument('-v', '--verbose', dest='verbose',
                        action='store_true', default=True,
                        help='Print status messages during operation.')
    args = parser.parse_args()

    # Ensure that we were provided an appropriate action
    if not args.action:
        exit_error('ERROR: an action must be specified!')
    else:
        if args.action != 'stop' and args.action != 'start':
            exit_error('ERROR: an invalid action was specified: ' + \
                       args.action + '!')
    action = args.action

    # Parse the cloud platforms to ensure that they are valid; build
    # a list of platforms and targets (region/resource group) for each
    if not args.platforms:
        exit_error('ERROR: at least one platform must be specified!')
    else:
        for platform in args.platforms.split(','):
            (provider, target) = platform.split(':')
            if provider not in ('aws', 'azure'):
                exit_error('ERROR: an invalid platform was specified: ' + \
                           provider + '!')
            else:
                if provider not in targets:
                    targets[provider] = []
                targets[provider].append(target)

    if args.verbose:
        verbose = True

    # Loop through each platform and apply the operation to all VMs in the
    # specified region or resource group

    for platform in targets:
        if platform == "aws":
            for target in targets[platform]:
                if verbose:
                    print('Performing ' + action + ' on VMs in AWS ' + target)
                manage_aws_hosts(action, target)

        if platform == "azure":
            for target in targets[platform]:
                if verbose:
                    print ('Performing ' + action + ' on VMs in Azure ' + target)
                manage_azure_hosts(action, target)