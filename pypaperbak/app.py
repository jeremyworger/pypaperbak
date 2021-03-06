
import argparse
import base64
import pyqrcode
import logging
import os
import zbarlight
import hashlib
import magic
import binascii
import sys

from pypaperbak.exporters import *
from pypaperbak.importers import *
import pypaperbak

class UnframeError(Exception):
    pass

class PyPaperbakApp:

    """Main app class for the PyPaperBak project."""

    def __init__(self):
        self.logger = logging.getLogger("pypaperback." + self.__class__.__name__)        

    def app_arguments(self):
        """Returns the argparse instance with a description 
        of the apps arguments."""
        parser = argparse.ArgumentParser(                
                description='Backup and restore from paper-backed datastore',
                prog='pypaperbak',
                epilog='%(prog)s available at https://github.com/matheusd/pypaperbak')
        parser.add_argument('action', choices=['backup', 'restore'], 
                            help='Action to perform ("backup" or "restore")',
                            metavar='ACTION')
        parser.add_argument('infile', 
                            help='Input file or dir',
                            nargs='?',
                            metavar='INFILE')
        parser.add_argument('outfile', 
                            help='Output file/directory',
                            nargs='?',
                            metavar='OUTFILE') 
        parser.add_argument('--exporter', 
                            choices=['pngdir', 'pdf'],
                            default='pngdir',
                            help='Exporter type. Options: {%(choices)s}. Default: %(default)s',
                            metavar='EXPORTER')      
        parser.add_argument('--baseoutfname', 
                            default='qr-%(qr_number)04d.png',
                            help='Base output filename when using a multi-file exporter. Default: "%(default)s"',
                            metavar='BASEOUTFNAME')              
        parser.add_argument('--pngscale', 
                            type=int,
                            default=2,
                            help='Scale for png output. Default: %(default)s',
                            metavar='SCALE')      
        parser.add_argument('--chunksize', 
                            type=int,
                            default=256,
                            help='How many bytes from the input to read when building a single QR code. Default: %(default)s',
                            metavar='CHUNKSIZE',
                            required=False)      
        parser.add_argument('--fnamepattern', 
                            default='qr-*.png',
                            required=False,
                            metavar='PATTERN',
                            help='Pattern of filenames to look for when importing from a dir. Default: %(default)s')
        parser.add_argument('--sha256',                                                         
                            action='store_true',
                            help='On backup, print the sha256 of the input file. On restore, print the sha256 of the restored file.')
        parser.add_argument('-v', '--verbose', 
                            action='store_true',
                            help='Generate verbose diagnostic to stderr')              
        parser.add_argument('--version',
                            action='version', 
                            version='%(prog)s ' + ('%s' % pypaperbak.__version__))
        return parser

       
    def main(self, argv):        
        """Entrypoint for the application."""
        arg_parser = self.app_arguments()
        args = arg_parser.parse_args(argv[1:])    
        self.run(args)

    def run(self, args):
        """Run the app given the decoded command line parameters."""        
        if args.verbose:
            logging.basicConfig(format="%(message)s", level=logging.INFO)            
        else:
            logging.basicConfig(format="%(message)s")                

        if args.action == 'backup':
            self.run_backup(args)
        elif args.action == 'restore':
            self.run_restore(args)
        else:
            raise Exception("Unimplemented action: %s" % args.action)

    def run_backup(self, args):
        """Run the backup operation."""                
        
        chunksize = args.chunksize
        encodefunc = base64.b85encode #FIXME: add arg        
        
        infile = open(args.infile, "rb")
        infile_size = os.path.getsize(infile.name)

        outfile = args.outfile        
        inputhash = hashlib.sha256() 
        framedata = self.frame_data_func(args)

        qr_count = infile_size / chunksize + 1
        self.logger.info('Original file size: %dKiB', infile_size / 1024)
        self.logger.info('Total number of QR codes: %d', qr_count)        

        exporter = self.setup_exporter(args, qr_count)

        qr_number = 0
        sizesofar = 0
        while True:
            bindata = infile.read(chunksize)
            if not bindata: break
            frame = framedata(bindata, qr_count, sizesofar)
            inputhash.update(bindata)
            sizesofar += len(bindata)

            qr_number += 1
            self.logger.info('Exporting QR %d of %d', qr_number, qr_count)
            
            encdata = encodefunc(frame).decode()            
            qr = pyqrcode.create(encdata)
            exporter.add_qr(qr)
                    
        exporter.finish(inputhash)
        self.logger.info('Finished exporting')
        if args.sha256: 
            print('SHA-256 of input: %s' % inputhash.hexdigest())

    def run_restore(self, args): 
        """Run the restore operation."""        

        if os.path.isdir(args.infile):
            self.logger.info('Setting up PngDirImporter')
            importer = PngDirImporter(args.infile, 
                                      args.fnamepattern)
        else:
            m = magic.Magic(mime=True, uncompress=True)
            ftype = m.from_file(args.infile)
            if ftype == "image/png":
                importer = ImageImporter(args.infile)
            else:
                raise Exception('Could not detect import type of file %s' % args.infile)

        decodefunc = base64.b85decode #FIXME: add arg        
    
        with open(args.outfile, "wb") as outfile:
            for image in importer:
                encdatalist = zbarlight.scan_codes('qrcode', image)                
                for encdata in encdatalist:
                    frame = decodefunc(encdata)
                    bindata, position = self.unframe_data(frame)                    
                    outfile.seek(position)
                    outfile.write(bindata)
        
        self.logger.info('Finished importing')

        # Cant calculate during writing because we may be reading pieces
        # out of order
        if args.sha256:
            hashfunc = hashlib.sha256()
            with open(args.outfile, "rb") as outfile:
                while True:
                    data = outfile.read()
                    if not data: break
                    hashfunc.update(data)
            print('SHA-256 of output: %s' % hashfunc.hexdigest())        
        

    def setup_exporter(self, args, qr_count):
        """Setup the exporter according to the specified args."""
        if args.exporter == 'pngdir':
            self.logger.info('Setting up PngDirExporter')
            exp = PngDirExporter(args.outfile, args.baseoutfname,
                                 args.pngscale)
        elif args.exporter == 'pdf':
            self.logger.info('Setting up PDFExporter')
            exp = PDFExporter(args.outfile, qr_count)
        else:
            raise Exception("Unimplemented exporter type: %s" % args.exporter)

        return exp

    def frame_data_func(self, args):
        """Prepare function that frames the data for a single qr code."""
        def v1(bindata, qrnumber, sizesofar):
            header = bytes([0xb1]) + sizesofar.to_bytes(4, 'big')
            crcheader = binascii.crc32(header)
            footer = binascii.crc32(bindata, crcheader).to_bytes(4, 'big')
            return header + bindata + footer

        
        return v1

    def unframe_data(self, bindata):        
        if (bindata[0] & 0xb0) != 0xb0:
            raise UnframeError("Binary data without magic number")
        version = bindata[0] & 0xf
        if version == 1:
            position = int.from_bytes(bindata[1:5], 'big')
            crcframe = int.from_bytes(bindata[-4:], 'big')
            crccalc = binascii.crc32(bindata[0:-4])
            # the crc is mostly unecessary when using qrcodes
            # this is mostly to make sure we're not reading a qr
            # code that is *not* a pypaperbak code.
            if crccalc != crcframe:
                raise UnframeError("CRC Checksum match error")
            return bindata[5:-4], position
        else:
            raise UnframeError("Unrecognized frame version: %d" % version)