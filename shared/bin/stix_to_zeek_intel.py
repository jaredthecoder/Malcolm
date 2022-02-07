#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2022 Battelle Energy Alliance, LLC.  All rights reserved.

import argparse
import errno
import logging
import os
import sys

from contextlib import nullcontext
from taxii2client.v20 import as_pages as TaxiiAsPages_v20
from taxii2client.v20 import Collection as TaxiiCollection_v20
from taxii2client.v20 import Server as TaxiiServer_v20
from taxii2client.v21 import as_pages as TaxiiAsPages_v21
from taxii2client.v21 import Collection as TaxiiCollection_v21
from taxii2client.v21 import Server as TaxiiServer_v21

import stix_zeek_utils

###################################################################################################
script_name = os.path.basename(__file__)
script_path = os.path.dirname(os.path.realpath(__file__))

###################################################################################################
# main
def main():
    parser = argparse.ArgumentParser(
        description='\n'.join(
            [
                'Outputs a Zeek intelligence framework file from "Indicator" objects in STIX™ v2.0/v2.1 JSON files.',
                '',
                'See:',
                ' - Zeek intelligence framework: https://docs.zeek.org/en/master/frameworks/intel.html',
                ' - Zeek intel types: https://docs.zeek.org/en/stable/scripts/base/frameworks/intel/main.zeek.html#type-Intel::Type',
                ' - STIX cyber-observable objects: https://docs.oasis-open.org/cti/stix/v2.1/cs01/stix-v2.1-cs01.html#_mlbmudhl16lr',
                ' - Malcolm documentation: https://github.com/idaholab/Malcolm#zeek-intelligence-framework',
                '',
                'Note: The Zeek intelligence framework only supports simple indicators matched against a single value.',
                'The STIX™ standard can express more complex indicators that cannot be expressed with Zeek intelligence items.',
            ]
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
        usage='{} <arguments>'.format(script_name),
    )
    parser.add_argument('--verbose', '-v', action='count', default=1, help='Increase verbosity (e.g., -v, -vv, etc.)')
    parser.add_argument(
        '--notice', dest='notice', action='store_true', help='Add fields for policy/frameworks/intel/do_notice.zeek'
    )
    parser.add_argument(
        '--no-notice',
        dest='notice',
        action='store_false',
        help='Do not add fields for policy/frameworks/intel/do_notice.zeek',
    )
    parser.set_defaults(notice=True)
    parser.add_argument(
        '--cif',
        dest='cif',
        action='store_true',
        help='Add fields for policy/integration/collective-intel/main.zeek',
    )
    parser.add_argument(
        '--no-cif',
        dest='cif',
        action='store_false',
        help='Do not add fields for policy/integration/collective-intel/main.zeek',
    )
    parser.set_defaults(cif=True)
    parser.add_argument(
        '-i',
        '--input',
        dest='input',
        nargs='*',
        type=str,
        default=None,
        help="STIX file(s), or TAXII 2.x URL(s), e.g., 'taxii|2.0|http://example.com/discovery|Collection Name|user|password'",
    )
    parser.add_argument(
        '--input-file',
        dest='inputFile',
        nargs='*',
        type=str,
        default=None,
        help="Read --input arguments from a local or external file (one per line)",
    )
    parser.add_argument(
        '-o',
        '--output',
        dest='output',
        type=str,
        default=None,
        help="Output file (stdout if unspecified)",
    )
    try:
        parser.error = parser.exit
        args = parser.parse_args()
    except SystemExit:
        parser.print_help()
        exit(2)

    args.verbose = logging.CRITICAL - (10 * args.verbose) if args.verbose > 0 else 0
    logging.basicConfig(
        level=args.verbose, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    )

    logging.info(os.path.join(script_path, script_name))
    logging.info("Arguments: {}".format(sys.argv[1:]))
    logging.info("Arguments: {}".format(args))
    if args.verbose > logging.DEBUG:
        sys.tracebacklimit = 0

    if args.input is None:
        args.input = []

    with open(args.output, 'w') if args.output is not None else nullcontext() as outfile:
        zeekPrinter = stix_zeek_utils.STIXParserZeekPrinter(args.notice, args.cif, file=outfile, logger=logging)

        # if --input-file is specified, process first and append to  --input
        for infileArg in args.inputFile:
            try:
                if os.path.isfile(infileArg):
                    # read inputs from local file
                    with open(infileArg) as f:
                        args.input.extend(f.read().splitlines())

                elif '://' in infileArg:
                    # download from URL and read input from remote file
                    with stix_zeek_utils.temporary_filename(suffix='.txt') as tmpFileName:
                        dlFileName = stix_zeek_utils.download_to_file(
                            infileArg,
                            local_filename=tmpFileName,
                            logger=logging,
                        )
                        if dlFileName is not None and os.path.isfile(dlFileName):
                            with open(dlFileName) as f:
                                args.input.extend(f.read().splitlines())

                else:
                    logging.warning(f"File '{infileArg}' not found")
            except Exception as e:
                logging.warning(f"{type(e).__name__} for '{infileArg}': {e}")

        # deduplicate input sources
        seenInput = {}
        args.input = [seenInput.setdefault(x, x) for x in args.input if x not in seenInput]
        logging.debug(f"Input: {args.input}")

        # process each given STIX input
        for inarg in args.input:
            try:
                with open(inarg) if ((inarg is not None) and os.path.isfile(inarg)) else nullcontext() as infile:

                    if infile:
                        zeekPrinter.ProcessSTIX(infile, source=os.path.splitext(os.path.basename(inarg))[0])

                    elif inarg.lower().startswith('taxii'):
                        # this is a TAXII URL, connect and retrieve STIX indicators from it
                        # taxii|2.0|discovery_url|collection_name|username|password
                        #
                        # examples of URLs I've used successfully for testing:
                        # - "taxii|2.0|https://cti-taxii.mitre.org/taxii/|Enterprise ATT&CK"
                        # - "taxii|2.0|https://limo.anomali.com/api/v1/taxii2/taxii/|CyberCrime|guest|guest"
                        #
                        # collection_name can be specified as * to retrieve all collections (careful!)

                        taxiiConnInfo = [stix_zeek_utils.base64_decode_if_prefixed(x) for x in inarg.split('|')[1::]]
                        taxiiVersion, taxiiDisoveryURL, taxiiCollectionName, taxiiUsername, taxiiPassword = (
                            None,
                            None,
                            None,
                            None,
                            None,
                        )
                        if len(taxiiConnInfo) >= 3:
                            taxiiVersion, taxiiDisoveryURL, taxiiCollectionName = taxiiConnInfo[0:3]
                        if len(taxiiConnInfo) >= 4:
                            taxiiUsername = taxiiConnInfo[3]
                        if len(taxiiConnInfo) >= 5:
                            taxiiPassword = taxiiConnInfo[4]

                        # connect to the server with the appropriate API for the TAXII version
                        if taxiiVersion == '2.0':
                            server = TaxiiServer_v20(taxiiDisoveryURL, user=taxiiUsername, password=taxiiPassword)
                        elif taxiiVersion == '2.1':
                            server = TaxiiServer_v21(taxiiDisoveryURL, user=taxiiUsername, password=taxiiPassword)
                        else:
                            raise Exception(f'Unsupported TAXII version "{taxiiVersion}"')

                        # collect the collection URL(s) for the given collection name
                        collectionUrls = {}
                        for api_root in server.api_roots:
                            for collection in api_root.collections:
                                if (taxiiCollectionName == '*') or (
                                    collection.title.lower() == taxiiCollectionName.lower()
                                ):
                                    collectionUrls[collection.title] = {
                                        'id': collection.id,
                                        'url': collection.url,
                                    }

                        # connect to and retrieve indicator STIX objects from the collection URL(s)
                        for title, info in collectionUrls.items():
                            collection = (
                                TaxiiCollection_v21(info['url'], user=taxiiUsername, password=taxiiPassword)
                                if taxiiVersion == '2.1'
                                else TaxiiCollection_v20(info['url'], user=taxiiUsername, password=taxiiPassword)
                            )
                            try:

                                # loop over paginated results
                                for envelope in (
                                    TaxiiAsPages_v21(
                                        collection.get_objects,
                                        per_request=stix_zeek_utils.TAXII_PAGE_SIZE,
                                        **stix_zeek_utils.TAXII_INDICATOR_FILTER,
                                    )
                                    if taxiiVersion == '2.1'
                                    else TaxiiAsPages_v20(
                                        collection.get_objects,
                                        per_request=stix_zeek_utils.TAXII_PAGE_SIZE,
                                        **stix_zeek_utils.TAXII_INDICATOR_FILTER,
                                    )
                                ):
                                    zeekPrinter.ProcessSTIX(
                                        envelope, source=':'.join([x for x in [server.title, title] if x is not None])
                                    )

                            except Exception as e:
                                logging.warning(f"{type(e).__name__} for object of collection '{title}': {e}")

            except Exception as e:
                logging.warning(f"{type(e).__name__} for '{inarg}': {e}")


###################################################################################################
if __name__ == '__main__':
    main()
