import sys, math, glob, multiprocessing, subprocess, os, bisect, random
from bioFiles import *

NUMPROC=1
NUMBINS=10
DISTANCE=200
OUTID=''
PERCENTILE=0.95
ADDCOUNT=1

# Usage: python3 heatmap_abs_bed_fr_pe.py [-p=num_proc] [-b=num_bins | -s=bin_size] [-d=distance] [-o=out_id] [-t=percentile] <gff_file> <fpkm_file> <bed_file> <input_bed_file>

def processInputs( gffFileStr, fpkmFileStr, bedFileStr, inputFileStr, numProc, numBins, binSize, distance, outID, percentile ):
	bedName = getSampleName( bedFileStr )
	inputName = getSampleName( inputFileStr )
	
	#info
	info = '#from_script: heatmap_abs_enrich_chip_fr_pe.py; '
	if numBins == -1:
		info += 'bin_size: {:d}; '.format( binSize )
		numBins = int( float(distance) / binSize )
	else:
		info += 'num_bins: {:d}; '.format( numBins )
		binSize = int( float(distance) / numBins )
	info += 'distance: {:d}; percentile: {:.1f}; gff_file: {:s}; fpkm_file: {:s}; enriched_bed_file: {:s}; input_bed_file: {:s}'.format( distance, percentile*100, os.path.basename(gffFileStr), os.path.basename(fpkmFileStr), os.path.basename(bedFileStr), os.path.basename(inputFileStr) )
	
	if outID == '':
		outFileStr =  'heat_abs_{:s}_{:s}_n{:d}_d{:d}.tsv'.format( bedName, inputName, numBins, distance )
	else:
		outFileStr = 'heat_abs_{:s}.tsv'.format( outID )
	
	print( 'GFF file:\t{:s}\nFPKM file:\t{:s}\nDistance:\t{:d}\nNum Bins:\t{:d}\nBin size:\t{:d}\nPercentile:\t{:.1f}\nEnrich file:\t{:s}\nInput file:\t{:s}\n'.format( os.path.basename(gffFileStr), os.path.basename(fpkmFileStr), distance, numBins, binSize, percentile*100, os.path.basename(bedFileStr), os.path.basename(inputFileStr) ))
	
	print( 'Reading FPKM file' )
	fpkmFile = FileFPKM( fpkmFileStr )
	fpkmAr = fpkmFile.getFPKMArray( )
	
	print( 'Reading GFF' )
	gffFile = FileGFF( gffFileStr )
	gffDict = gffFile.getGeneDict()
	
	print( 'Begin processing with {:d} processors'.format( numProc ) )
	pool = multiprocessing.Pool( processes=numProc )
	results = [ pool.apply_async( processFile, args=(f, gffDict, fpkmAr, distance, numBins, binSize ) ) for f in [bedFileStr, inputFileStr] ]
	outMats = [ p.get() for p in results ]
	matrixBed = outMats[0][0]
	genesBed = outMats[0][1]
	matrixInput = outMats[1][0]
	genesInput = outMats[1][1]
	
	# enrichment
	print( 'Calculating enrichment...' )
	enrichMat, numList = calculateEnrichment( matrixBed, matrixInput )
	# get threshold
	threshold = calculatePercentile( percentile, numList )
	# write output
	print( 'Writing output to {:s}'.format( outFileStr ) )
	writeOutput( outFileStr, enrichMat, genesBed, threshold, info )
	print( 'Done' )
	

def getSampleName( fileStr ):
	leftIndex = fileStr.rfind('/')
	rightIndex = fileStr.rfind('.')
	sampleName = fileStr[leftIndex+1:rightIndex]
	return sampleName
	
def processFile( bedFileStr, gffDict, fpkmAr, distance, numBins, binSize ):
	
	print( 'Processing {:s}'.format( os.path.basename(bedFileStr) ) )
	# get BED information
	bedFile = FileBED_FR( bedFileStr )
	bedDict, bpCount = bedFile.getBedDict( )
	#del( bedFile )
	
	print( 'Analyzing {:s}'.format( os.path.basename(bedFileStr) ) )
	outMatrix = processBed( bedDict, gffDict, fpkmAr, distance, numBins, binSize, bpCount )
	
	return outMatrix
	

def processBed( bedDict, gffDict, fpkmAr, distance, numBins, binSize, bpCount ):
	outMatrix = []
	geneList = []
	
	# loop through genes
	for gene in fpkmAr:
		info = gffDict.get( gene )
		if info == None:
			continue
		chrm, start, end, strand = info
		chrmDict = bedDict.get( chrm )
		if (end - start + 1) < distance or chrmDict == None:
			continue
		# only have genes that are long enough now
		
		tssStart = start - distance
		tssEnd = start + distance
		tss = countRegion( chrmDict, tssStart, start, tssEnd, numBins, binSize )
		
		ttsStart = end - distance
		ttsEnd = end + distance
		tts = countRegion( chrmDict, ttsStart, end, ttsEnd, numBins, binSize )
		
		# handle negative strand genes
		if strand == '-':
			tss.reverse()
			tts.reverse()
			outAr = tts + tss
		# positive genes
		else:
			outAr = tss + tts
		outAr = [ x / bpCount * 1000000 for x in outAr ]
		outMatrix.append( outAr )
		geneList += [ gene ]
	# end for gene
	return outMatrix, geneList

def countRegion( bedDict, start, mid, stop, numBins, binWidth ):
	
	upAr = [ADDCOUNT] * numBins
	downAr = [ADDCOUNT] * numBins
	if bedDict == None:
		return -1
	# upstream Ar
	for bin in range( numBins ):
		bStart = int(start + bin*binWidth)
		# loop through each position
		for pos in range(bStart, int(bStart+binWidth)):
			dictEntry = bedDict.get( pos )
			if dictEntry != None:
				upAr[bin] += dictEntry
	
	# downstream Ar
	for bin in range(numBins):
		bStart = int(mid+1 + bin*binWidth)
		for pos in range(bStart, int(bStart+binWidth)):
			dictEntry = bedDict.get( pos )
			if dictEntry != None:
				downAr[bin] += dictEntry
	outAr = upAr + downAr
	return outAr
	
def calculateEnrichment( bedMatrix, inputMatrix ):
	
	if len( bedMatrix ) != len( inputMatrix ):
		print( 'ERROR: Matrices are not the same size\nBED Matrix has {:d} genes.\nInput Matrix has {:d} genes.'.format( len(bedMatrix), len(inputMatrix)) )
		exit()
	enrichMatrix = []
	numList = []
	# iterate through genes
	for i in range( len(bedMatrix) ):
		if len(bedMatrix[i]) != len(inputMatrix[i]):
			print( 'ERROR: Matrices are not the same size\n(row {:d})\nBED matrix has {:d} columns.\nInput matrix has {:d} columns.'.format(i, len(bedMatrix[i]),len(inputMatrix[i]) ) )
			exit()
		tmp = [ (bedMatrix[i][j])/(inputMatrix[i][j]) for j in range( len(bedMatrix[i]) ) ]
		enrichMatrix.append( tmp )
		numList += tmp
	return enrichMatrix, numList

def calculatePercentile( percentile, numList ):
	
	if percentile == 1:
		return max( numList )
	numList.sort()
	ind = math.ceil( percentile * len( numList ) - 1 )
	try:
		p = numList[ind]
	except IndexError:
		return numList[-1]
	if p == 0:
		print( '***** not percentile corrected *****' )
		return numList[-1]
	return p

def writeOutput( outFileStr, outMatrix, geneNames, threshold, info ):
	
	outFile = open( outFileStr, 'w' )
	header = '\ngene.name\tgene.num\tbin\tvalue\n'
	outFile.write( info +'\n'+ header )
	
	# loop through genes
	for i in range(len(outMatrix)):
		geneName = geneNames[i]
		geneNum = 'G{:08}'.format( len(geneNames) - i )
		valueAr = outMatrix[i]
		
		nBins = int(len(valueAr) / 2)
		if nBins % 2 != 0:
			print ( 'error: nbins/2' )
		
		# loop through tss bins
		for j in range(nBins):
			outStr = '{:s}\t{:s}\t{:d}\t{:f}\n'.format( geneName, geneNum, j, (threshold if valueAr[j] > threshold else valueAr[j]) )
			outFile.write( outStr )
		
		# add intermediate rows
		m = int(math.floor(nBins/8.0))
		mm = max(m, 3)
		for j in range(mm):
			outFile.write( '{:s}\t{:s}\t{:d}\tNA\n'.format( geneName, geneNum, nBins+j ) )
		for j in range(mm):
			outFile.write('{:s}\t{:s}\t{:d}\tNA\n'.format( geneName, geneNum, nBins+mm+j ) )
		
		# loop through tts bins
		for j in range(nBins):
			outStr = '{:s}\t{:s}\t{:d}\t{:f}\n'.format( geneName, geneNum, j+nBins+(2*mm), (threshold if valueAr[j+nBins] > threshold else valueAr[j+nBins] ) )
			outFile.write( outStr )
	# end loop
	outFile.close()

def parseInputs( argv ):

	numProc = NUMPROC
	binSize = -1
	numBins = -1
	distance = DISTANCE
	outID = OUTID
	percentile = PERCENTILE
	startInd = 0
	
	for i in range(len(argv)-3):
		if argv[i].startswith( '-p=' ):
			try:
				numProc = int( argv[i][3:] )
				startInd += 1
			except ValueError:
				print( 'ERROR: number of processors must be an integer' )
				exit()
		elif argv[i].startswith( '-b=' ):
			# check for previous '-s'
			if binSize != -1:
				print( 'ERROR: cannot specify -b and -s together' )
				exit()
			try:
				numBins = int( argv[i][3:] )
				startInd += 1
				isNumBins = True
			except ValueError:
				print( 'ERROR: number of bins must be an integer' )
				exit()
		elif argv[i].startswith( '-s=' ):
			# check previous '-b'
			if numBins != -1:
				print( 'ERROR: cannot specify -b and -s together' )
				exit()
			try:
				binStr = argv[i][3:]
				if binStr.endswith( 'k' ) or binStr.endswith( 'K' ):
					binSize = int( binStr[:-1] ) * 1000
				elif binStr.endswith( 'm' ) or binStr.endswith( 'M' ):
					binSize = int( binStr[:-1] ) * 1000000
				else:
					binSize = int( binStr )
				startInd += 1
			except ValueError:
				print( 'ERROR: bin size must be an integer' )
				exit()
		elif argv[i].startswith( '-t=' ):
			try:
				percentile = float( argv[i][3:] )
				if percentile > 1:
					percentile /= 100
				startInd += 1
			except ValueError:
				print( 'ERROR: percentile must be numeric' )
				exit()
		elif argv[i].startswith( '-o=' ):
			outID = argv[i][3:]
			startInd += 1
		elif argv[i].startswith( '-d=' ):
			try:
				distance = int( argv[i][3:] )
				startInd += 1
			except ValueError:
				print( 'ERROR: distance must be an integer' )
				exit()
		elif argv[i].startswith('-'):
			print( 'ERROR: {:s} is not a valid option'.format( argv[i] ) )
			exit()
	# end for
	
	if numBins == -1 and binSize == -1:
		numBins = NUMBINS
	
	if numBins == -1 and distance % binSize != 0:
		nnbins = int( math.ceil( float(distance) / binSize ) )
		distance = nnbins * binSize
		print( 'WARNING: bin size doesn\'t evenly divide into distance...adjusting distance to {:s}'.format(distance) )
	elif binSize == -1 and distance % numBins != 0:
		nnsize = int( math.ceil( float(distance) / numBins ) )
		distance = nnsize * numBins
		print( 'WARNING: num bins doesn\'t evenly divide into distance...adjusting distance to {:s}'.format(distance) )
		
	gffFileStr = argv[startInd]
	fpkmFileStr = argv[startInd+1]
	bedFileStr = argv[startInd+2]
	inputFileStr = argv[startInd+3]
	
	processInputs( gffFileStr, fpkmFileStr, bedFileStr, inputFileStr, numProc, numBins, binSize, distance, outID, percentile )

if __name__ == "__main__":
	if len(sys.argv) < 4:
		print ("Usage: python3 heatmap_abs_bed_fr_pe.py [-p=num_proc] [-b=num_bins | -s=bin_size] [-d=distance] [-o=out_id] [-t=percentile] <gff_file> <fpkm_file> <bed_file> [bed_file]*")
	else:
		parseInputs( sys.argv[1:] )
