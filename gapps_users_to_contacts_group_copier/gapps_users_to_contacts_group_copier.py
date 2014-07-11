#!/usr/bin/env python

"""A script that copies select Calendar Resources as contacts under a set group for selected organization's members."""
__author__ = "vsin"
__version__ = "1.0.0"

import gdata.apps.client
import atom.data
import gdata.contacts.data
import gdata.contacts.client

import interactive_gapps_auth

import sys
import os.path
import os
import logging
import logging.config
import ConfigParser
import optparse
import random
from fnmatch import fnmatch
import json
import urllib

options = None
config = None

# Set of those Contact field relation values that are overwritten by the script
SYNC_ORG_RELS = set([gdata.data.WORK_REL, gdata.data.MOBILE_REL])
# Set of those Contact field label values that are overwritten by the script
SYNC_ORG_LABELS = set(["Employee ID"])

APP_CONFIG_SECTION = "application"
PARAM_CONFIG_SECTION = "default_params"
DIRECTORY_URI = "https://www.googleapis.com/admin/directory/v1/users?"
DOMAIN = None
DEFAULT_REL = gdata.data.WORK_REL
DEFAULT_EXTERNAL_ID_REL = u'organization'
OPTOUT_URI = "https://intra.futurice.com/u/contacts/api/get_opted_out"
OPTOUT_SETTING = "optout_employees"
EXTPROP_CONTACT_ID_NAME = None
EXTPROP_CONTACT_SOURCE_NAME = None
EXTPROP_CONTACT_SOURCE_VALUE = None
EXTPROP_CONTACT_RENAMED_NAME = None
EXTPROP_CONTACT_RENAMED_VALUE = None
EXTPROP_GROUP_NAME = None
EXTPROP_GROUP_VALUE = None
MY_CONTACTS_ID = "Contacts"

contacts_client = None
apps_client = None
domain_token = None
request_feed = None

#
# Helpers:
#

def parse_options():
    global options

    parser = optparse.OptionParser()

    parser.add_option(
        "-S", "--select-pattern",
        dest="select_pattern",
        help="select users to copy by pattern GLOB against user's email address",
        metavar="GLOB")

    parser.add_option(
        "-P", "--no-phone",
        dest="phone",
        action="store_false",
        default=True,
        help="copy also those users who do NOT have a phone number set (default: only copy users who have a phone number set)")

    parser.add_option(
        "-U", "--user-pattern",
        dest="user_pattern",
        help="copy contacts to all users whose email address matches GLOB",
        metavar="GLOB")

    parser.add_option(
        "-G", "--group",
        dest="group",
        help="copy contacts under group NAME",
        metavar="NAME")
    
    parser.add_option(
        "-M", "--my-contacts",
        dest="my_contacts",
        action="store_true",
        default=False,
        help="add contacts to My Contacts as well")
        
    parser.add_option(
        "-D", "--delete-old",
        dest="delete_old",
        action="store_true",
        default=False,
        help="also check the target group for old contacts added by this script and delete those")

    parser.add_option(
        "-R", "--rename-old",
        dest="rename_old",
        action="store_true",
        default=False,
        help="also check the target group for old contacts added by this script and rename them with a suffix")
        
    parser.add_option(
        "--rename-suffix",
        dest="rename_suffix",
        help="suffix string to use in conjunction with --rename-old",
        metavar="SUFFIX")

    parser.add_option(
        "--add-other-emails",
        dest="add_other_emails",
        action="store_true",
        default=False,
        help="also add all user's other e-mail addresses to contacts [default: only add the primary email]")

    parser.add_option(
        "--add-aliases",
        dest="add_aliases",
        action="store_true",
        default=False,
        help="also add all user's e-mail aliases to contacts [default: only add the primary email]")
    
    parser.add_option(
        "-O", "--organization-name",
        dest="organization_name",
        help="organization name to use for contacts when not specified",
        metavar="NAME")
        
    parser.add_option(
        "--undo",
        dest="undo",
        action="store_true",
        default=False,
        help="remove all groups and contacts added by this script [dangerous]")
    

    parser.add_option(
        "-r", "--reauth",
        dest="reauth",
        action="store_true",
        default=False,
        help="reauthorize Google Account")

    parser.add_option(
        "-b", "--batch",
        dest="batch",
        action="store_true",
        default=False,
        help="batch operation (consider interactive reauthorization an error)")

    parser.add_option(
        "-t", "--token",
        dest="token_file",
        help="use OAuth2 token FILE [default: %default]",
        metavar="FILE",
        default="token.conf")

    parser.add_option(
        "-d", "--domain-token",
        dest="domain_file",
        help="use domain token FILE [default: %default]",
        metavar="FILE",
        default="token_domain.conf")

    parser.add_option(
        "-c", "--config",
        dest="config",
        help="read application configuration from FILE [default: %default]",
        metavar="FILE",
        default="config.conf")

    parser.add_option(
        "-l", "--log-config",
        dest="log_config",
        help="read logging configuration from FILE [default: %default]",
        metavar="FILE",
        default="logging.conf")

    options = parser.parse_args()[0]

def read_config():
    global config, DOMAIN, DEFAULT_REL, EXTPROP_CONTACT_ID_NAME, \
        EXTPROP_CONTACT_SOURCE_NAME, EXTPROP_CONTACT_SOURCE_VALUE, \
        EXTPROP_CONTACT_RENAMED_NAME, EXTPROP_CONTACT_RENAMED_VALUE, \
        EXTPROP_GROUP_NAME, EXTPROP_GROUP_VALUE

    config = ConfigParser.RawConfigParser()
    config.read(options.config)

    # Set default params
    if config.has_section(PARAM_CONFIG_SECTION):
        for param in config.options(PARAM_CONFIG_SECTION):
            if not hasattr(options, param) or getattr(options, param) is None:
                setattr(options, param, config.get(PARAM_CONFIG_SECTION, param))

    DOMAIN = config.get(APP_CONFIG_SECTION, "domain")
    if config.has_option(APP_CONFIG_SECTION, "default_rel"):
        DEFAULT_REL = config.get(APP_CONFIG_SECTION, "default_rel")

    EXTPROP_CONTACT_ID_NAME = config.get(APP_CONFIG_SECTION, "contact_id_extended_property_name")
    EXTPROP_CONTACT_SOURCE_NAME = config.get(APP_CONFIG_SECTION, "contact_source_extended_property_name")
    EXTPROP_CONTACT_SOURCE_VALUE = config.get(APP_CONFIG_SECTION, "contact_source_extended_property_value")
    EXTPROP_CONTACT_RENAMED_NAME = config.get(APP_CONFIG_SECTION, "contact_renamed_extended_property_name")
    EXTPROP_CONTACT_RENAMED_VALUE = config.get(APP_CONFIG_SECTION, "contact_renamed_extended_property_value")
    EXTPROP_GROUP_NAME = config.get(APP_CONFIG_SECTION, "group_extended_property_name")
    EXTPROP_GROUP_VALUE = config.get(APP_CONFIG_SECTION, "group_extended_property_value")

def get_optout_set():
    """Returns a set of user-names who wish to opt-out from synchronization."""

    optout_json = json.load(urllib.urlopen(OPTOUT_URI))
    if u'settings' in optout_json and \
        unicode(OPTOUT_SETTING) in optout_json[u'settings']:
        optout_users = set(map(lambda user_email: user_email.lower(), optout_json[u'settings'][u'optout_employees']))
        logging.debug("Optout users: %s" % optout_users)
        return optout_users

    logging.error("Could not understand opt-out data format")
    sys.exit(1)

def init_clients():
    global contacts_client, apps_client, domain_token, request_feed

    # Get tokens
    domain_token = interactive_gapps_auth.obtain_domain_token(
        token_file=options.domain_file)

    try:
        admin_token = interactive_gapps_auth.obtain_oauth2_token(
            token_file=options.token_file,
            scopes=config.get(APP_CONFIG_SECTION, "oauth_scopes"),
            client_id=config.get(APP_CONFIG_SECTION, "client_id"),
            client_secret=config.get(APP_CONFIG_SECTION, "client_secret"),
            user_agent=config.get(APP_CONFIG_SECTION, "user_agent"),
            reauth=options.reauth,
            batch=options.batch)
    except interactive_gapps_auth.ReAuthRequiredError:
        logging.error("Re-authorization required but --batch was specified.")
        sys.exit(1)

    contacts_client = gdata.contacts.client.ContactsClient(DOMAIN, auth_token=domain_token)

    apps_client = gdata.apps.client.AppsClient(DOMAIN)
    admin_token.authorize(apps_client)
    apps_client.ssl = True

    request_feed = gdata.contacts.data.ContactsFeed()

def ACTING_AS(email):
    """Sets domain token user."""
    logging.info('Domain token now acting as %s', email)
    domain_token.requestor_id = email

def get_current_user():
    """Gets domain token user."""
    return domain_token.requestor_id

def get_ldap_id_json(json_user):
    if u'externalIds' in json_user:
        ldapIds = [ extid[u'value'] for extid in json_user[u'externalIds'] if u'customType' in extid and extid[u'customType'] == u'Employee ID' and u'value' in extid ]
        if ldapIds:
            return ldapIds[0]
    return None

def get_ldap_id_contact(contact):
    ldapIds = [ extprop.value for extprop in contact.extended_property if extprop.name == EXTPROP_CONTACT_ID_NAME ]
    if ldapIds:
        return ldapIds[0]
    return None

def select_users():
    """Select users by set options."""
    users_to_copy = []
    target_user_emails = []
    
    next_page = None
    while True:
        uri_params = {
            "domain": DOMAIN,
            "maxResults": 500
        }
        if next_page is not None: uri_params['pageToken'] = next_page

        response = json.load(apps_client.request("GET", DIRECTORY_URI + urllib.urlencode(uri_params)))

        if u'users' in response:
            for user in response[u'users']:
                if u'primaryEmail' in user:
                    if fnmatch(user[u'primaryEmail'], options.user_pattern):
                        target_user_emails.append(user[u'primaryEmail'])
                    if fnmatch(user[u'primaryEmail'], options.select_pattern) and \
                        (not options.phone or ( \
                            u'phones' in user and \
                            any([ u'value' in phone and phone[u'value'] for phone in user[u'phones'] ]) ) \
                        ) and get_ldap_id_json(user):
                        users_to_copy.append(user)

        if u'nextPageToken' in response: next_page = response[u'nextPageToken']
        else: break

    return (users_to_copy, target_user_emails)

def get_magic_group(groups, create=True):
    for group in groups:
        if is_script_group(group):
            # Found group, get members
            contacts_query = gdata.contacts.client.ContactsQuery()
            contacts_query.group = group.id.text
            contacts_query.max_results = config.getint(APP_CONFIG_SECTION, "max_contacts")
            return (group, contacts_client.get_contacts(q=contacts_query).entry)

    if not create:
        return (None, [])
    
    # No group found, create
    logging.info('%s: No domain contact group found, creating..', get_current_user())
    new_group = gdata.contacts.data.GroupEntry()
    new_group.title = atom.data.Title(options.group)

    # Set extended property
    extprop = gdata.data.ExtendedProperty()
    extprop.name = EXTPROP_GROUP_NAME
    extprop.value = EXTPROP_GROUP_VALUE
    new_group.extended_property.append(extprop)

    return (contacts_client.CreateGroup(new_group=new_group), [])

def submit_batch(force=False):
    global request_feed
    
    if not force and len(request_feed.entry) < config.getint(APP_CONFIG_SECTION, "batch_max"):
        return # Wait for more requests

    result_feed = contacts_client.execute_batch(request_feed)
    for result in result_feed.entry:
        try: status_code = int(result.batch_status.code)
        except ValueError: status_code = -1
        if status_code < 200 or status_code >= 400:
            logging.warn("%s: Error %d (%s) while %s'ing batch ID %s = %s (%s)",
                get_current_user(),
                status_code,
                result.batch_status.reason,
                result.batch_operation.type,
                result.batch_id.text,
                result.id and result.id.text or result.get_id(),
                result.name and result.name.full_name and result.name.full_name or "name unknown")

    # Return next feed
    request_feed = gdata.contacts.data.ContactsFeed()

submit_batch_final = lambda: submit_batch(force=True)

# Return if contact was added by the script
is_script_contact = lambda contact: len(filter(
    lambda extprop: extprop.name == EXTPROP_CONTACT_SOURCE_NAME and extprop.value == EXTPROP_CONTACT_SOURCE_VALUE,
    contact.extended_property)) > 0

# Return if contact was renamed by the script
is_renamed_contact = lambda contact: len(filter(
    lambda extprop: extprop.name == EXTPROP_CONTACT_RENAMED_NAME and extprop.value == EXTPROP_CONTACT_RENAMED_VALUE,
    contact.extended_property)) > 0
    
# Return if contact group was added by the script
is_script_group = lambda group: len(filter(
    lambda extprop: extprop.name == EXTPROP_GROUP_NAME and extprop.value == EXTPROP_GROUP_VALUE,
    group.extended_property)) > 0

# Rename contact with "deleted" suffix
def add_suffix(contact):
    if contact.name.name_suffix and contact.name.name_suffix.text:
        old_suffix = contact.name.name_suffix.text + " "
    else: old_suffix = ""
    contact.name.name_suffix = gdata.data.NameSuffix(old_suffix + options.rename_suffix)
    contact.name.full_name = gdata.data.FullName(contact.name.full_name.text + " " + options.rename_suffix)

    # Add ext prop to signal that this contact has been renamed by the script
    extprop = gdata.data.ExtendedProperty()
    extprop.name = EXTPROP_CONTACT_RENAMED_NAME
    extprop.value = EXTPROP_CONTACT_RENAMED_VALUE
    contact.extended_property.append(extprop)

# Re-rename contact to remove "deleted" suffix
def remove_suffix(contact):
    contact.name.name_suffix = None
    contact.name.full_name = gdata.data.FullName(contact.name.given_name.text + " " + contact.name.family_name.text)
    contact.extended_property = [ extprop for extprop in contact.extended_property if extprop.name != EXTPROP_CONTACT_RENAMED_NAME ]

#
# JSON converters:
#

type_to_rel_mapper = {
    u'work': gdata.data.WORK_REL,
    u'home': gdata.data.HOME_REL,
    u'other': gdata.data.OTHER_REL,
    u'work_fax': gdata.data.HOME_FAX_REL,
    u'home_fax': gdata.data.WORK_FAX_REL,
    u'other_fax': gdata.data.OTHER_FAX_REL,
    u'mobile': gdata.data.MOBILE_REL,
    u'work_mobile': gdata.data.WORK_MOBILE_REL,
    u'pager': gdata.data.PAGER_REL,
    u'work_pager': gdata.data.WORK_PAGER_REL,
    u'compain_main': gdata.data.COMPANY_MAIN_REL, # Typo in documentation?
    u'company_main': gdata.data.COMPANY_MAIN_REL, 
    u'assistant': gdata.data.ASSISTANT_REL,
    u'car': gdata.data.CAR_REL,
    u'radio': gdata.data.RADIO_REL,
    u'isdn': gdata.data.ISDN_REL,
    u'callback': gdata.data.CALLBACK_REL,
    u'telex': gdata.data.TELEX_REL,
    u'tty_tdd': gdata.data.TTL_TDD_REL,
    u'ttl_tdd': gdata.data.TTL_TDD_REL, # Another typo?
    u'main': gdata.data.MAIN_REL
}

def set_rel_or_label(obj, json):
    # https://developers.google.com/google-apps/contacts/v3/reference#Elements
    if u'type' in json:
        if json[u'type'] == u'custom':
            if u'customType' in json and json[u'customType']:
                obj.label = json[u'customType']
            else:
                obj.rel = gdata.data.OTHER_REL
            return
        if json[u'type'] in type_to_rel_mapper:
            obj.rel = type_to_rel_mapper[json[u'type']]
        else:
            obj.rel = gdata.data.OTHER_REL
    else:
        obj.rel = DEFAULT_REL

def json_to_email_object(json):
    email_object = gdata.data.Email()
    set_rel_or_label(email_object, json)

    email_object.address = json[u'address']

    return email_object

def json_to_organization_object(json):
    org_object = gdata.data.Organization()
    set_rel_or_label(org_object, json)

    if u'name' in json: org_object.name = gdata.data.OrgName(json[u'name'])
    elif options.organization_name: org_object.name = gdata.data.OrgName(options.organization_name)
    if u'title' in json: org_object.title = gdata.data.OrgTitle(json[u'title'])
    if u'department' in json: org_object.department = gdata.data.OrgDepartment(json[u'department'])
    if u'symbol' in json: org_object.symbol = gdata.data.OrgSymbol(json[u'symbol'])

    return org_object

def json_to_phone_number_object(json):
    phone_number_object = gdata.data.PhoneNumber(json[u'value'])
    set_rel_or_label(phone_number_object, json)
    
    if u'primary' in json and json[u'primary']: phone_number_object.primary = "true"
    
    return phone_number_object

def json_to_external_id_object(json):
    ext_id_object = gdata.contacts.data.ExternalId()

    ext_id_object.value = json[u'value']

    if u'type' in json:
        if json[u'type'] == u'custom':
            if u'customType' in json and json[u'customType']:
                ext_id_object.label = json[u'customType']
            else: ext_id_object.rel = DEFAULT_EXTERNAL_ID_REL
        else: ext_id_object.rel = json[u'type']
    else: ext_id_object.rel = DEFAULT_EXTERNAL_ID_REL

    return ext_id_object

def json_to_postal_address_object(json):
    # We only support formatted "blob" type addresses (no structure)
    postal_address_object = gdata.data.PostalAddress(json[u'formatted'])
    set_rel_or_label(postal_address_object, json)

    if u'primary' in json and json[u'primary']: postal_address_object.primary = "true"

    return postal_address_object

protocol_mapper = {
    u'aim': gdata.data.AIM_PROTOCOL,
    u'gtalk': gdata.data.GOOGLE_TALK_PROTOCOL,
    u'icq': gdata.data.ICQ_PROTOCOL,
    u'jabber': gdata.data.JABBER_PROTOCOL,
    u'qq': gdata.data.QQ_PROTOCOL,
    u'skype': gdata.data.SKYPE_PROTOCOL,
    u'yahoo': gdata.data.YAHOO_MESSENGER_PROTOCOL
}

def json_to_im_object(json):
    im_object = gdata.data.Im()
    set_rel_or_label(im_object, json)

    if u'protocol' in json:
        if json[u'protocol'] == u'custom_protocol':
            if u'customProtocol' in json:
                im_object.protocol = json[u'customProtocol']
            elif json[u'protocol'] in protocol_mapper:
                im_object.protocol = protocol_mapper[json[u'protocol']]
            else: im_object.protocol = json[u'protocol']
    
    if u'primary' in json and json[u'primary']: im_object.primary = "true"
    im_object.address = json[u'im']

    return im_object

def json_to_contact_object(json):
    new_contact = gdata.contacts.data.ContactEntry()

    # Set the contact name
    new_contact.name = gdata.data.Name(
        given_name=gdata.data.GivenName(text=json[u'name'][u'givenName']),
        family_name=gdata.data.FamilyName(text=json[u'name'][u'familyName']),
        full_name=gdata.data.FullName(text=json[u'name'][u'fullName']))

    # Set the contact email address
    new_contact.email.append(gdata.data.Email(address=json[u'primaryEmail'],
        primary='true', display_name=json[u'name'][u'fullName'], rel=DEFAULT_REL))

    # Add aliases
    if options.add_aliases:
        if u'aliases' in json:
            for alias in json[u'aliases']:
                new_contact.email.append(gdata.data.Email(address=alias,
                    primary='false', display_name=json[u'name'][u'fullName'], rel=DEFAULT_REL))
        if u'nonEditableAliases' in json:
            for alias in json[u'nonEditableAliases']:
                new_contact.email.append(gdata.data.Email(address=alias,
                    primary='false', display_name=json[u'name'][u'fullName'], rel=DEFAULT_REL))

    # Add other emails
    if options.add_other_emails and u'emails' in json:
        for json_email in json[u'emails']:
            if u'address' in json_email:
                email_object = json_to_email_object(json_email)
                email_object.display_name = json[u'name'][u'fullName']
                email_object.primary = 'false'
                new_contact.email.append(email_object)

    # Add organization (job title) info
    if u'organizations' in json and len(json[u'organizations']) > 0:
        for json_org in json[u'organizations']:
            if u'primary' in json_org and json_org[u'primary']:
                primary_org = json_org
                break
        else: primary_org = json[u'organizations'][0]
        org_object = json_to_organization_object(primary_org)
        org_object.primary = "true"
        new_contact.organization = org_object
        
    elif options.organization_name:
        # Add at least our org name
        org_object = gdata.data.Organization()
        # the API requires exactly one of 'rel' or 'label'
        org_object.rel = DEFAULT_REL
        org_object.name = gdata.data.OrgName(options.organization_name)
        org_object.primary = "true"
        new_contact.organization = org_object

    if u'phones' in json:
        for json_phone in json[u'phones']:
            if u'value' in json_phone:
                new_contact.phone_number.append(json_to_phone_number_object(json_phone))
        
    if u'externalIds' in json:
        for json_external_id in json[u'externalIds']:
            if u'value' in json_external_id:
                new_contact.external_id.append(json_to_external_id_object(json_external_id))
                
    if u'addresses' in json:
        for json_address in json[u'addresses']:
            if u'formatted' in json_address and json_address[u'formatted']:
                new_contact.postal_address.append(json_to_postal_address_object(json_address))

    if u'ims' in json:
        for json_im in json[u'ims']:
            if u'im' in json_im:
                new_contact.im.append(json_to_im_object(json_im))
                
    return new_contact

is_sync_field = lambda obj: obj.rel in SYNC_ORG_RELS or obj.label in SYNC_ORG_LABELS

def sync_contact(source, target):
    """Copies data from source contact to target contact and returns True if target was modified."""

    modified = False

    # Notes
    if source.content and source.content.text:
        if not target.content or target.content.text != source.content.text:
            modified = True
            target.content = source.content

    # Name
    if source.name:
        if not target.name:
            modified = True
            target.name = source.name

        if source.name.family_name and (not target.name.given_name or target.name.given_name.text != source.name.given_name.text):
            modified = True
            target.name.given_name = source.name.given_name

        if source.name.family_name and (not target.name.family_name or target.name.family_name.text != source.name.family_name.text):
            modified = True
            target.name.family_name = source.name.family_name

        if source.name.full_name and (not target.name.full_name or target.name.full_name.text != source.name.full_name.text):
            modified = True
            target.name.full_name = source.name.full_name

    # Organization
    if source.organization:
        if not target.organization:
            modified = True
            target.organization = gdata.data.Organization()

        if source.organization.name and (not target.organization.name or target.organization.name.text != source.organization.name.text):
            modified = True
            target.organization.name = source.organization.name

        if source.organization.title and (not target.organization.title or target.organization.title.text != source.organization.title.text):
            modified = True
            target.organization.title = source.organization.title

        if source.organization.department and (not target.organization.department or target.organization.department.text != source.organization.department.text):
            modified = True
            target.organization.department = source.organization.department

        if source.organization.symbol and (not target.organization.symbol or target.organization.symbol.text != source.organization.symbol.text):
            modified = True
            target.organization.symbol = source.organization.symbol

        if source.organization.rel and target.organization.rel != source.organization.rel:
            modified = True
            target.organization.rel = source.organization.rel

        if source.organization.label and target.organization.label != source.organization.label:
            modified = True
            target.organization.label = source.organization.label

    # Emails
    source_sync_emails = set([ email.address for email in source.email if is_sync_field(email) ])
    target_sync_emails = set([ email.address for email in target.email if is_sync_field(email) ])
    if source_sync_emails != target_sync_emails:
        modified = True
        # There can be only one primary email. Prefer user's choice.
        target.email = [ email for email in target.email if not is_sync_field(email) ]
        has_private_primary = any([email.primary == "true" for email in target.email])
        source_sync_emails = [ email for email in source.email if is_sync_field(email) ]

        if has_private_primary:
            def remove_primary(email):
                if email.primary != "true": return email
                copy = gdata.data.Email()
                copy.address = email.address
                copy.rel = email.rel
                copy.label = email.label
                return copy
            target.email.extend(map(remove_primary, source_sync_emails))
        else:
            target.email.extend(source_sync_emails)

    # Phones
    source_sync_phone_numbers_tuples = set([ (phone_number.text, phone_number.rel) for phone_number in source.phone_number if is_sync_field(phone_number) ])
    target_sync_phone_numbers_tuples = set([ (phone_number.text, phone_number.rel) for phone_number in target.phone_number if is_sync_field(phone_number) ])

    if source_sync_phone_numbers_tuples != target_sync_phone_numbers_tuples:
        modified = True
        # There can be only one primary phone. Prefer user's choice.
        target.phone_number = [ phone_number for phone_number in target.phone_number if not is_sync_field(phone_number) ]
        has_private_primary = any([phone_number.primary == "true" for phone_number in target.phone_number])
        source_sync_phone_numbers = [ phone_number for phone_number in source.phone_number if is_sync_field(phone_number) ]

        if has_private_primary:
            def remove_primary(phone_number):
                if phone_number.primary != "true": return phone_number
                copy = gdata.data.PhoneNumber(phone_number.text)
                copy.rel = phone_number.rel
                copy.label = phone_number.label
                return copy
            target.phone_number.extend(map(remove_primary, source_sync_phone_numbers))
        else:
            target.phone_number.extend(source_sync_phone_numbers)

    # External IDs
    source_sync_external_ids = set([ external_id.value for external_id in source.external_id if is_sync_field(external_id) ])
    target_sync_external_ids = set([ external_id.value for external_id in target.external_id if is_sync_field(external_id) ])
    if source_sync_external_ids != target_sync_external_ids:
        modified = True
        target.external_id = [ external_id for external_id in target.external_id if not is_sync_field(external_id) ]
        target.external_id.extend([ external_id for external_id in source.external_id if is_sync_field(external_id) ])

    # Addresses
    source_sync_addresses = set([ address.text for address in source.postal_address if is_sync_field(address) ])
    target_sync_addresses = set([ address.text for address in target.postal_address if is_sync_field(address) ])
    if source_sync_addresses != target_sync_addresses:
        modified = True
        # There can be only one primary address. Prefer user's choice.
        target.postal_address = [ address for address in target.postal_address if not is_sync_field(address) ]
        has_private_primary = any([address.primary == "true" for address in target.postal_address])
        source_sync_addresses = set([ address for address in source.postal_address if is_sync_field(address) ])

        if has_private_primary:
            def remove_primary(address):
                if address.primary != "true": return address
                copy = gdata.data.PostalAddress(address.text)
                copy.rel = address.rel
                copy.label = address.label
                return copy
            target.postal_address.extend(map(remove_primary, source_sync_addresses))
        else:
            target.postal_address.extend(source_sync_addresses)

    # IMs
    source_sync_ims = set([ im.protocol + "://" + im.address for im in source.im if is_sync_field(im) ])
    target_sync_ims = set([ im.protocol + "://" + im.address for im in target.im if is_sync_field(im) ])
    if source_sync_ims != target_sync_ims:
        modified = True
        # There can be only one primary IM. Prefer user's choice.
        target.im = [ im for im in target.im if not is_sync_field(im) ]
        has_private_primary = any([im.primary == "true" for im in target.im])
        source_sync_ims = set([ im for im in source.im if is_sync_field(im) ])

        if has_private_primary:
            def remove_primary(im):
                if im.primary != "true": return im
                copy = gdata.data.Im()
                copy.protocol = im.protocol
                copy.address = im.address
                copy.rel = im.rel
                copy.label = im.label
                return copy
            target.im.extend(map(remove_primary, source_sync_ims))
        else:
            target.im.extend(source_sync_ims)

    return modified
    
def undo(target_user):
    # Let's delete users by global list and group list on the off chance the global list
    # is not comprehensive due to its size exceeding query limits.
    removed_ids = set()

    contacts = contacts_client.get_contacts().entry
    for contact in contacts:
        if is_script_contact(contact):
            logging.info('%s: Removing auto-generated contact "%s" with ID %s',
                get_current_user(), contact.name.full_name.text, contact.id.text)
            removed_ids.add(contact.id.text)
            request_feed.add_delete(entry=contact)
            submit_batch()

    # Get users' groups
    groups = contacts_client.get_groups().entry

    # Find group by extended property
    (magic_group, magic_group_members) = get_magic_group(groups, create=False)
    if magic_group is not None:
        for group_member in magic_group_members:
            if group_member.id.text not in removed_ids and is_script_contact(group_member):
                logging.info('%s: Removing auto-generated contact "%s" with ID %s',
                    get_current_user(), group_member.name.full_name.text, group_member.id.text)
                request_feed.add_delete(entry=group_member)
                submit_batch()

        # Remove group
        contacts_client.delete_group(magic_group)
        logging.info('%s: Removing auto-generated group "%s" with ID %s',
            get_current_user(), magic_group.title.text, magic_group.id.text)

def get_value_by_contact_email(email_dict, contact):
    """Resolve contact object to email key in email_dict and return the first matching value."""

    # Get all emails with a match in dictionary
    matching_emails = filter(
        lambda email: email.address and email.address.lower() in email_dict,
        contact.email
    )

    if len(matching_emails) == 0: return None

    # Get primary work emails
    contact_emails = filter(
        lambda email: email.primary == 'true' and email.rel == DEFAULT_REL,
        matching_emails
    )

    if len(contact_emails) == 0:
        # No primary work email? Get non-primary work emails
        contact_emails = filter(
            lambda email: email.rel == DEFAULT_REL,
            matching_emails
        )

    if len(contact_emails) == 0:
        # No work email? Get primary emails
        contact_emails = filter(
            lambda email: email.primary == 'true',
            matching_emails
        )

    if len(contact_emails) == 0:
        # No primary email? Get all matching emails
        contact_emails = matching_emails

    if len(contact_emails) > 1: logging.warn('%s: Several matching emails (%s) for contact "%s" with ID %s',
        get_current_user(),
        map(lambda email: email.address, contact_emails),
        contact.name and contact.name.full_name and contact.name.full_name.text or "(unknown)",
        contact.id and contact.id.text)

    return email_dict[contact_emails[0].address.lower()]

#
# Main routine:
#

def main():
    # Set-up
    os.chdir(os.path.dirname(sys.argv[0]))
    parse_options()
    logging.config.fileConfig(options.log_config)

    try: main_logging()
    except Exception, err:
        logging.exception("Caught exception:")
        sys.exit(1)

def main_logging():
    read_config()
    init_clients()

    if options.delete_old and options.rename_old:
        logging.error("Conflicting options detected, aborting")
        sys.exit(1)

    # Get opt-out lists
    optout_emails_set = get_optout_set()
    
    # Select domain users by options
    (users_to_copy, target_user_emails) = select_users()
    target_user_emails = filter(lambda user_email: user_email.lower() not in optout_emails_set, target_user_emails)
    user_to_copy_by_ldap_dict = dict(zip([get_ldap_id_json(user_json) for user_json in users_to_copy], users_to_copy))

    if len(users_to_copy) == 0:
        logging.warn("Zero users to copy, aborting")
        sys.exit(0)

    if len(target_user_emails) == 0:
        logging.warn("Zero target users found, aborting")
        sys.exit(0)

    logging.info('Starting Directory to Contacts Group copy operation. Selection is "%s" (%d user(s)) and target is "%s" (%d user(s))',
        options.select_pattern, len(users_to_copy), options.user_pattern, len(target_user_emails))

    random.shuffle(target_user_emails)
    for target_user_email in target_user_emails:
        # Act as the selected user
        ACTING_AS(target_user_email)

        if options.undo:
            undo(target_user_email)
            continue
        
        # Get users' groups
        groups = contacts_client.get_groups().entry

        # Find group by extended property
        (magic_group, magic_group_members) = get_magic_group(groups)
        magic_group_ldaps_set = filter(None, [ get_ldap_id_contact(contact) for contact in magic_group_members ])

        # Find My Contacts group
        my_contacts_group = filter(lambda group: group.system_group and group.system_group.id == MY_CONTACTS_ID, groups)
        if my_contacts_group: my_contacts_group = my_contacts_group[0]

        logging.info('%s: Using group called "%s" with %d members and ID %s',
            get_current_user(), magic_group.title.text,
            len(magic_group_members), magic_group.id.text)

        # Add new users (not already in the group) as contacts
        for user_to_copy in users_to_copy:
            if get_ldap_id_json(user_to_copy) not in magic_group_ldaps_set:
                new_contact = json_to_contact_object(user_to_copy)
                
                # Add the relevant groups
                new_contact.group_membership_info.append(gdata.contacts.data.GroupMembershipInfo(href=magic_group.id.text))
                if options.my_contacts and my_contacts_group:
                    new_contact.group_membership_info.append(gdata.contacts.data.GroupMembershipInfo(href=my_contacts_group.id.text))
                
                # Set extended properties
                new_contact.extended_property.append(gdata.data.ExtendedProperty(name=EXTPROP_CONTACT_ID_NAME, value=get_ldap_id_json(user_to_copy)))
                new_contact.extended_property.append(gdata.data.ExtendedProperty(name=EXTPROP_CONTACT_SOURCE_NAME, value=EXTPROP_CONTACT_SOURCE_VALUE))

                logging.debug('%s: Creating contact "%s"',
                    get_current_user(), new_contact.name.full_name.text)
                request_feed.add_insert(new_contact)
                submit_batch()
        
        # Sync data for existing contacts that were added by the script and remove those that have been deleted
        for existing_contact in magic_group_members:
            if is_script_contact(existing_contact):
                if get_ldap_id_contact(existing_contact) in user_to_copy_by_ldap_dict:
                    user_to_copy = user_to_copy_by_ldap_dict[get_ldap_id_contact(existing_contact)]
                    modified = False

                    if options.rename_old and is_renamed_contact(existing_contact):
                        # Remove renamed flag
                        remove_suffix(existing_contact)
                        modified = True

                    # Sync data
                    dir_contact = json_to_contact_object(user_to_copy)
                    modified = sync_contact(dir_contact, existing_contact) or modified

                    if modified:
                        logging.info('%s: Modifying contact "%s" with ID %s',
                            get_current_user(), existing_contact.name.full_name.text, existing_contact.id.text)

                        # Batch version (fails in Jul 2014 'Error 403 If-Match or If-None-Match header or entry etag attribute required')
                        #request_feed.add_update(existing_contact)
                        #submit_batch()

                        # One-by-one (non-batch) version:
                        try:
                            contacts_client.update(existing_contact)
                        except:
                            logging.exception('While updating 1 contact:')
                else:
                    # Surplus contact
                    if options.delete_old:
                        logging.info('%s: Removing surplus auto-generated contact "%s" with ID %s',
                            get_current_user(), existing_contact.name.full_name.text, existing_contact.id.text)
                        request_feed.add_delete(entry=existing_contact)
                        submit_batch()
                    elif options.rename_old and not is_renamed_contact(existing_contact):
                        old_name = existing_contact.name.full_name.text
                        add_suffix(existing_contact)
                        logging.info('%s: Renaming surplus auto-generated contact "%s" to "%s" with ID %s',
                            get_current_user(), old_name, existing_contact.name.full_name.text, existing_contact.id.text)

                        # Batch version (same as above "Error 403 If-Match or If-None-Match header or entry etag")
                        #request_feed.add_update(existing_contact)
                        #submit_batch()

                        # One-by-one (non-batch) version:
                        try:
                            contacts_client.update(existing_contact)
                        except:
                            logging.exception('While updating 1 contact:')

        submit_batch_final()

if __name__ == "__main__":
    main()
