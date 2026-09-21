import json
import shlex
import re
import os
import sys
import argparse
import pprint
import pickle
from collections import OrderedDict
from typing import Generator

"""
author James Smith 2025
requires all categories to be separated by #
does not retain comments
    possible errors on encountering comments if resembles category terminator symbol
does not retain "." as opposed to "?" (deconverts to ?)
for datablocks > 1, splits into separate json files
fixed
    did not retain dictionary order
    negative numbers converting to strings
    multiline strings
    modularize
    strings inconsistently quoted
    retain data blocks > 1
    enable extended ids (no fix required?)
tasks:
    possible errors on encountering comments if resembles category terminator symbol
    does not retain "." as opposed to "?" (deconverts to ?)
"""

def generator(filepath:str) -> Generator[str, None, None]:
    """read cif file line by line
    args:
        filepath (str): cif file path
    returns:
        line (str): line from cif file
    raises:
        StopIteration: on reading end of file
    """
    for line in open(filepath, "r"):
        line = line.rstrip()
        yield line

def get_next_value(s:str) -> str | float | int | None:
    """convert value of mmcif attribute to typed json value
    args:
        s (str): value to convert
    returns:
        converted value
    """
    if re.match(r"^[-]?\d*\.\d+$", s):
        return float(s)
    elif s.isdigit():
        return int(s)
    elif s == "?" or s == ".":
        return None
    tokens = s.split()
    if len(tokens) == 1:
        return s
    # optionally quote multi-word string
    # return "'%s'" % s.replace("'", "")
    return s

def convert_multiline_string(line:str, gen:Generator) -> str:
    """convert mmcif multiline string to json string with newlines
    args:
        line (str): string to convert
        gen (Generator): generator for cif file
    returns:
        converted string with only internal newlines
    """
    multiline = line[1:]
    multiline += "\n"
    # a blank line is legal inside a multiline value, so do not stop on ""
    while True:
        line = next(gen)
        if line.startswith(";"):
            break
        multiline += line
        multiline += "\n"
    return multiline[:-1]

def format_outfile_path(outfile:str, datablocks:int) -> str:
    """expand file names for datablocks > 1
    args:
        outfile (str): output file path
        datablocks (int): number of datablocks found
    returns:
        str: output file path
    """
    datablock = datablocks - 1
    outfilename = os.path.basename(outfile)
    outfilename = os.path.splitext(outfilename)[0] + "-" + str(datablock) + os.path.splitext(outfilename)[1]
    outfilepath = os.path.join(os.path.dirname(outfile), outfilename)
    return outfilepath

def skip_category(category_name:str) -> bool:
    if category_name.startswith("_"):
        category_name = category_name[1:]
    skips = ["atom_site"]
    if category_name in skips:
        return True
    return False

def converter(infile:str, outfile:str, unit_cardinality:bool, skip_coords:bool=False) -> list:
    """convert cif to python dictionary
    args:
        infile (str): cif file path
        outfile (str): output temporary pickle file path
        skip_coords (bool): do not convert coordinates to json
    returns:
        list: list of temporary pickle file paths
    raises:
        StopIteration: on reading end of file
        ValueError: if more than one datablock found
    """

    gen = generator(infile)

    template = OrderedDict()

    datablocks = 0

    pklfiles = []

    try:
        while True:
            line = next(gen)
            if line == "":
                # blank line outside a multiline value: ignore
                continue
            if line.startswith("data_"):
                datablocks += 1
                if datablocks > 1:
                    print("datablock %d" % (datablocks - 1))
                    outfilepath = format_outfile_path(outfile, datablocks)
                    with open(outfilepath, "wb") as w:
                        pickle.dump(template, w)
                    pklfiles.append(outfilepath)
                    print("wrote temporary result to %s" % outfilepath)
                    template = OrderedDict()
                continue
            elif line.startswith("#"):
                continue
            elif line.startswith("_"):
                # read all attributes and values
                attributes = OrderedDict()
                category = None
                while line.startswith("_"):
                    tokens = shlex.split(line)
                    # test for multiline record
                    while len(tokens) < 2:
                        line = next(gen)
                        if line == "":
                            continue
                        # test for multiline value
                        if line.startswith(";"):
                            multiline = convert_multiline_string(line, gen)
                            tokens.append(multiline)
                        else:
                            tokens.extend(shlex.split(line))
                    if len(tokens) > 2:
                        raise ValueError("too many values for %s: %s" % (tokens[0], tokens[1:]))
                    # extract keys
                    category, attribute = tokens[0].split(".")
                    if category.startswith("_"):
                        category = category[1:]
                    if category not in template:
                        if unit_cardinality:
                            template[category] = OrderedDict()
                        else:
                            # make an array of one object, as if it were a loop
                            template[category] = []
                    # extract value
                    value = tokens[1]
                    # convert strings to numbers or ? to null
                    value = get_next_value(value)
                    # add to attributes
                    attributes[attribute] = value
                    line = next(gen)
                if unit_cardinality:
                    template[category].update(attributes)
                else:
                    # array of one object
                    template[category].append(attributes)
            elif line.startswith("loop_"):
                line = next(gen)
                if not line.startswith("_"):
                    raise ValueError("unexpected character after loop record")
                category, attribute = line.split(".")
                if skip_coords and skip_category(category):
                    while line.startswith("_"):
                        line = next(gen)
                    while not line.startswith("#"):
                        line = next(gen)
                    continue
                attributes = []
                # read all attributes
                while line.startswith("_"):
                    category, attribute = line.split(".")
                    if category.startswith("_"):
                        category = category[1:]
                    # make empty table
                    if category not in template:
                        template[category] = []
                    # add attribute
                    attributes.append(attribute)
                    line = next(gen)
                # read all data records
                while not line.startswith("#"):
                    if line == "":
                        line = next(gen)
                        continue
                    # read all values in one record
                    values = shlex.split(line)
                    # test for multiline record
                    while len(values) < len(attributes):
                        # continue until have all values
                        line = next(gen)
                        if line == "":
                            continue
                        # test for multiline value
                        if line.startswith(";"):
                            # continue until multiline value completes
                            multiline = convert_multiline_string(line, gen)
                            # add multiline value
                            values.append(multiline)
                        else:
                            tokens = shlex.split(line)
                            # add values
                            values.extend(tokens)
                    if len(values) > len(attributes):
                        raise ValueError("record for %s has %d values but %d attributes at line %s"
                                         % (category, len(values), len(attributes), line))
                    # add record to template
                    record = OrderedDict()
                    for attribute, value in zip(attributes, values):
                        # convert strings to numbers or ? to null
                        value = get_next_value(value)
                        record.update({attribute: value})
                    template[category].append(record)
                    line = next(gen)
            else:
                raise ValueError("error - unrecognized line %s" % line)
    except StopIteration:
        if datablocks > 1:
            datablocks += 1
            print("datablock %d" % (datablocks - 1))
            outfilepath = format_outfile_path(outfile, datablocks)
            with open(outfilepath, "wb") as w:
                pickle.dump(template, w)
            pklfiles.append(outfilepath)
            print("wrote temporary result to %s" % outfilepath)
        else:
            with open(outfile, "wb") as w:
                pickle.dump(template, w)
            pklfiles.append(outfile)
            print("wrote temporary result to %s" % outfile)
    except ValueError as e:
        print("Value Error: " + str(e))
        return []
    except Exception as e:
        print("Exception: " + str(e))
        return []

    return pklfiles


def cif2json(infile:str, outfile:str, skip_coords:bool=False, unit_cardinality:bool=False, dictionary:bool=False) -> bool:

    if not os.path.exists(infile):
        print("error - file %s does not exist" % infile)
        return False

    # find name for data block
    inlabel = os.path.splitext(os.path.basename(infile).upper())[0]
    outlabel = os.path.splitext(os.path.basename(outfile).upper())[0]
    # assert inlabel == outlabel, "input and output filenames must be the same %s %s" % (inlabel, outlabel)

    # convert cif file to python dictionary
    # write result to pickle file
    if (pklfiles := converter(infile, outfile, unit_cardinality, skip_coords)) == []:
        print("error - conversion failed")
        return False

    # read dictionary from pickle file
    for resultfile in pklfiles:
        with open(resultfile, "rb") as r:
            data = pickle.load(r)
        # remove pickle file
        os.unlink(resultfile)
        # convert to json or formatted dictionary
        # write result to resultfile
        if not dictionary:
            with open(resultfile, "w") as w:
                json.dump(data, w, indent=4)
        else:
            with open(resultfile, "w") as w:
                w.write(pprint.pformat(data))
        print("wrote final result to %s" % resultfile)

    print("conversion complete")
    return True
