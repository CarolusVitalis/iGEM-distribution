import os
import logging
import urllib.request
import glob
from urllib.error import HTTPError

from Bio import Entrez, SeqIO
import sbol2
import sbol3
from sbol_utilities.helper_functions import flatten
from .directories import EXPORT_DIRECTORY,SBOL_EXPORT_NAME
from .package_specification import package_stem


GENBANK_CACHE_FILE = 'GenBank_imports.gb'
IGEM_SBOL2_CACHE_FILE = 'iGEM_SBOL2_imports.xml'
IGEM_SBOL3_CACHE_FILE = 'iGEM_SBOL3_imports.nt'
IGEM_FASTA_CACHE_FILE = 'iGEM_raw_imports.fasta'

FASTA_iGEM_PATTERN = 'http://parts.igem.org/cgi/partsdb/composite_edit/putseq.cgi?part={}'
SBOL_iGEM_PATTERN = 'https://synbiohub.org/public/igem/BBa_{}'
NCBI_PREFIX = 'https://www.ncbi.nlm.nih.gov/nuccore/'


def sbol_uri_to_accession(uri: str, prefix: str = NCBI_PREFIX) -> str:
    """Change an NCBI SBOL URI to an NCBI accession
    :param uri: to convert
    :param prefix: prefix to use with accession, defaulting to NCBI nuccore
    :return: equivalent accession ID
    """
    return uri.removeprefix(prefix).replace('_','.')


def accession_to_sbol_uri(accession: str, prefix: str = NCBI_PREFIX) -> str:
    """Change an NCBI accession ID to an equivalent NCBI SBOL URI
    :param accession: to convert
    :param prefix: prefix to use with accession, defaulting to NCBI nuccore
    :return: equivalent URI
    """
    if not prefix.endswith('/'):
        prefix += '/'
    return f'{prefix}{accession.replace(".","_")}'


def retrieve_genbank_accessions(ids: list[str],package):
    """Retrieve a set of nucleotide accessions from GenBank
    Returns
    -------
    String containing the retrieved set of GenBank records
    """
    # GenBank pull:
    Entrez.email = 'engineering@igem.org'
    id_string = ','.join([sbol_uri_to_accession(i) for i in ids])  # Have to strip everything but the accession
    print(f'Attempting to retrieve {len(ids)} parts from NCBI: {id_string}')
    try:
        handle = Entrez.efetch(id=id_string, db='nucleotide', rettype='gb', retmode='text')
        retrieved = [r for r in SeqIO.parse(handle, 'gb')]
        # add retrieved records to cache
        cache_file = os.path.join(package,GENBANK_CACHE_FILE)
        print(f'Retrieved {len(retrieved)} records from NCBI; writing to {cache_file}')
        with open(cache_file,'a') as out:
            for r in retrieved:
                out.write(r.format('gb'))
        return [accession_to_sbol_uri(r.id) for r in retrieved] # add the accessions back in
    except HTTPError:
        print('NCBI retrieval failed')


def retrieve_igem_parts(ids,package):
    """Retrieve a set of iGEM parts from SynBioHub when possible, direct from the Registry when not.

    Returns
    -------
    Pair of SBOL2 Document with parts from SynBioHub, string with FASTA for parts from Registry
    """
    sbh_source = sbol2.partshop.PartShop('https://synbiohub.org')
    doc = sbol2.Document()
    cache_file = os.path.join(package,GENBANK_CACHE_FILE)
    fasta = ''
    # pull one at a time, because SynBioHub will give an error if we try to pull multiple and one is missing
    retrieved = []
    for i in ids:
        try:
            url = SBOL_iGEM_PATTERN.format(i)
            logging.info(f'Attempting to retrieve from SynBioHub: {url}')
            sbh_source.pull(url, doc)
            retrieved.append(i)
        except sbol2.SBOLError as err:
            logging.info(f'Could not retrieve from SynBioHub')
            if err.error_code() == sbol2.SBOLErrorCode.SBOL_ERROR_NOT_FOUND:
                try:
                    url = FASTA_iGEM_PATTERN.format(i)
                    logging.info(f'Attempting to retrieve from iGEM Registry: {url}')
                    with urllib.request.urlopen(url,timeout=5) as f:
                        captured = f.read().decode('utf-8')
                        fasta += captured
                    retrieved.append(i)
                except IOError:
                    logging.info('Could not retrieve from iGEM Registry')
    return doc, fasta

source_list = {
    NCBI_PREFIX: retrieve_genbank_accessions,
    'https://synbiohub.org': retrieve_igem_parts,
    'http://parts.igem.org': retrieve_igem_parts
}


def retrieve_parts(ids: list[str],package) -> list[str]:
    """Attempt to download parts from various servers

    :param ids: list of URIs
    :return: list of URIs successfully retrieved
    """
    "Attempt to collect all of the parts on the list"
    collected = []
    for prefix,retriever in source_list.items():
        matches = [i for i in ids if i.startswith(prefix)]
        if len(matches)>0:
            successes = retriever(matches,package)
            collected += successes
    return collected

# Test:
# retrieve_parts('iGEM',{'J23101','J23106'})
# retrieve_parts('GB',{'JWYZ01000115.1','PVOS01000173.1'})

extensions = {
    'FASTA': {'*.fasta','*.fa'},
    'GenBank': {'*.genbank','*.gb'},
    'SBOL2': {'*.xml'},
    'SBOL3': {sbol3.NTRIPLES:{'*.nt'},
              sbol3.RDF_XML:{'*.rdf'},
              sbol3.TURTLE:{'*.ttl'},
              sbol3.JSONLD:{'*.json','*.jsonld'}
              }
}

def package_parts_inventory(package: str) -> list[str]:
    """Search all of the SBOL, GenBank, and FASTA files of a package to find what parts have been downloaded

    :param package: path of package to search
    :return: list of URIs
    """
    inventory = []

    # import FASTAs and GenBank
    for file in flatten(glob.glob(os.path.join(package, ext)) for ext in extensions['FASTA']):
        prefix = package_stem(package)
        with open(file) as f:
            for record in SeqIO.parse(f, "fasta"):
                inventory.append(accession_to_sbol_uri(record.id,prefix))

    for file in flatten(glob.glob(os.path.join(package, ext)) for ext in extensions['GenBank']):
        is_ncbi_cache = os.path.basename(file) == GENBANK_CACHE_FILE
        prefix = NCBI_PREFIX if is_ncbi_cache else package_stem(package)
        with open(file) as f:
            for record in SeqIO.parse(f, "gb"):
                inventory.append(accession_to_sbol_uri(record.id,prefix))

    # import SBOL2
    for file in flatten(glob.glob(os.path.join(package, ext)) for ext in extensions['SBOL2']):
        doc = sbol2.Document()
        doc.read(file)
        inventory += [obj.persistentIdentity for obj in doc if isinstance(obj,sbol2.ComponentDefinition)]

    # import SBOL3
    for rdf_type,patterns in extensions['SBOL3'].items():
        for file in flatten(glob.glob(os.path.join(package, ext)) for ext in patterns):
            doc = sbol3.Document()
            doc.read(file)
            inventory += [obj.identity for obj in doc.objects if isinstance(obj,sbol3.Component)]

    return inventory

# TODO: switch to sbol_utilities constants at sbol-utilities 1.05a
BASIC_PARTS_COLLECTION = 'BasicParts'
COMPOSITE_PARTS_COLLECTION = 'CompositeParts'
LINEAR_PRODUCTS_COLLECTION = 'LinearDNAProducts'
FINAL_PRODUCTS_COLLECTION = 'FinalProducts'

def import_parts(package: str):
    # First collect the package specification
    package_spec = sbol3.Document()
    package_spec.read(os.path.join(package,EXPORT_DIRECTORY,SBOL_EXPORT_NAME))
    package_parts = [p.lookup() for p in package_spec.find(BASIC_PARTS_COLLECTION).members]

    print(f'  Package specification contains {len(package_parts)} parts')

    # Then collect the parts in the package directory
    inventory_parts = package_parts_inventory(package)
    print(f'  Found {len(inventory_parts)} available parts')

    # Compare the parts lists to each other to figure out which elements are missing
    package_part_ids = {p.identity for p in package_parts}
    package_sequence_ids = {p.identity for p in package_parts if p.sequences}
    package_no_sequence_ids = {p.identity for p in package_parts if not p.sequences}
    inventory_part_ids = set(inventory_parts)
    both = package_part_ids & inventory_part_ids
    #package_only = package_part_ids - inventory_part_ids # not actually needed?
    inventory_only = inventory_part_ids - package_part_ids
    missing_sequences = package_no_sequence_ids - inventory_part_ids
    print(f' {len(package_sequence_ids)} have sequences in Excel, {len(both)} found in directory, {len(missing_sequences)} not found')
    print(f' {len(inventory_only)} parts in directory are not used in package')
    if inventory_only:
        print(f' Found {len(inventory_only)} unused parts:' + " ".join(p for p in inventory_only))

    # attempt to retrieve missing parts
    if len(missing_sequences) == 0:
        print('No missing sequences')
        return []
    else:
        print('Attempting to download missing parts')
        download_list = list(missing_sequences)
        download_list.sort()
        retrieved = retrieve_parts(download_list,package)
        print(f'Retrieved {len(retrieved)} out of {len(missing_sequences)} missing sequences')
        print(retrieved)
        still_missing = missing_sequences - set(retrieved)
        if still_missing:
            print('Still missing:'+"".join(f' {p}\n' for p in still_missing))
        return retrieved