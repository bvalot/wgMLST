#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Add a strain to wgMLST database"""

import sys
import os
import argparse
import sqlite3
from Bio import SeqIO
import tempfile
import subprocess
import shutil
from lib.psl import Psl,testCDS

blat_exe = "blat"

desc = "Add a strain to the wgMLST database"
command = argparse.ArgumentParser(prog='mlst_add_strain.py', \
    description=desc, usage='%(prog)s [options] genome database')
command.add_argument('-s', '--strain', nargs='?', \
    type=str, default=None, \
    help='Name of the strain (default:genome name)')
command.add_argument('-i', '--identity', nargs='?', \
    type=float, default=0.95, \
    help='Minimun identity to search gene (default=0.95)')
command.add_argument('-c', '--coverage', nargs='?', \
    type=float, default=0.9, \
    help='Minimun coverage to search gene (default=0.9)')
command.add_argument('-p', '--path', nargs='?', \
    type=str, default="/usr/bin", \
    help='Path to BLAT executable (default=/usr/bin)')
command.add_argument('genome', \
    type=argparse.FileType("r"), \
    help='Genome of the strain')
command.add_argument('database', \
    type=argparse.FileType("r"), \
    help='Sqlite database to stock MLST')

def create_coregene(cursor, tmpfile):
    ref = "ref"
    cursor.execute('''SELECT gene, seqid FROM mlst WHERE souche=?''', (ref,))
    all_rows = cursor.fetchall()
    coregenes = []
    for row in all_rows:
        cursor.execute('''SELECT sequence FROM sequences WHERE id=?''', (row[1],))
        seq = cursor.fetchone()[0]
        tmpfile.write('>' + row[0] + "\n" + seq + "\n")
        coregenes.append((row[0], seq))
    return coregenes

def insert_sequence(cursor, sequence):
    try:
        cursor.execute('''INSERT INTO sequences(sequence) VALUES(?)''', (sequence,))
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        cursor.execute('''SELECT id FROM sequences WHERE sequence=?''', (sequence,))
        return cursor.fetchone()[0]

def run_blat(path, name, genome, tmpfile, tmpout, identity, coverage):
    command = [path+blat_exe, '-maxIntron=20', '-fine', '-minIdentity='+str(identity*100),\
               genome.name, tmpfile.name, tmpout.name]
    proc = subprocess.Popen(command, stderr=subprocess.PIPE, stdout=sys.stdout)
    error = ""
    for line in iter(proc.stderr.readline,''):
        error += line
    if error != "":
        sys.stdout.write("Error during runnin BLAT\n")
        raise Exception(error)
    genes = {}
    for line in open(tmpout.name, 'r'):
        try:
            int(line.split()[0])
        except:
            continue
        psl = Psl(line)
        if psl.coverage >=coverage and psl.coverage <= 1:
            genes.setdefault(psl.geneId(),[]).append(psl)
    if len(genes) == 0:
        raise Exception("No path found for the coregenome")
    return genes

def read_genome(genome):
    seqs = {}
    for seq in SeqIO.parse(genome, 'fasta'):
        seqs[seq.id] = seq
    return seqs
    
if __name__=='__main__':
    """Performed job on execution script""" 
    args = command.parse_args()    
    database = args.database
    genome = args.genome
    name = args.strain
    if args.identity<0 or args.identity > 1:
        raise Exception("Identity must be between 0 to 1")
    path = args.path
    if path:
        path = path.rstrip("/")+"/"
        if os.path.exists(path+blat_exe) is False:
            raise Exception("BLAT executable not found in folder: \n"+path)
    if name is None:
        name = genome.name.split('/')[-1]
    tmpfile = tempfile.NamedTemporaryFile(mode='w+t', suffix='.fasta', delete=False)
    tmpout = tempfile.NamedTemporaryFile(mode='w+t', suffix='.psl', delete=False)
    tmpout.close()
    
    try:
        db = sqlite3.connect(database.name)
        cursor = db.cursor()
        cursor2 = db.cursor()

        ##verify strain not already on the database
        cursor.execute('''SELECT DISTINCT souche FROM mlst WHERE souche=?''', (name,))
        if cursor.fetchone() is not None:
            raise Exception("Strain is already present in database:\n"+name)
        
        ##read coregene
        coregenes = create_coregene(cursor, tmpfile)
        tmpfile.close()

        ##BLAT analysis
        sys.stderr.write("Search coregene with BLAT\n")
        genes = run_blat(path, name, genome, tmpfile, tmpout, args.identity, args.coverage)
        sys.stderr.write("Finish run BLAT, found " + str(len(genes)) + " genes\n")
        
        ##add sequence MLST
        seqs = read_genome(genome)
        bad = 0
        for coregene in coregenes:
            if coregene[0] not in genes:
                continue
            for gene in genes.get(coregene[0]):
                seq = seqs.get(gene.chro, None)
                if seq is None:
                    raise Exception("Chromosome ID not found " + gene.chro)

                ##Correct coverage
                if gene.coverage != 1:
                    if gene.searchPartialCDS(seq, args.coverage) is False:
                        sys.stderr.write("Gene " + gene.geneId() + " partial: removed\n")
                        bad += 1
                        continue
                    else:
                        sys.stderr.write("Gene " + gene.geneId() + " fill: added\n")

                ##Verify CDS
                if testCDS(gene.getSequence(seq), False) is False:
                    if gene.searchCorrectCDS(seq, args.coverage) is False:
                        sys.stderr.write("Gene " + gene.geneId() + " not correct: removed\n")
                        bad += 1
                        continue
                    else:
                        sys.stderr.write("Gene " + gene.geneId() + " correct: added\n")
 
                ##add sequence and MLST
                sequence = gene.getSequence(seq)
                
                ##Insert data in database
                seqid = insert_sequence(cursor, str(sequence).upper())
                cursor2.execute('''INSERT INTO mlst(souche, gene, seqid) VALUES(?,?,?)''', \
                                    (name, gene.geneId(), seqid))
        db.commit()
        sys.stderr.write("Add " + str(len(genes)-bad) + " new MLST gene to database\n")
        sys.stderr.write("FINISH\n")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        if os.path.exists(tmpfile.name):        
            os.remove(tmpfile.name)
        if os.path.exists(tmpout.name):        
            os.remove(tmpout.name)
