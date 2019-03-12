import sys, math, glob, multiprocessing, subprocess, os
import pandas as pd
import numpy as np
import scipy.stats as stats
from functools import partial
import pandas_util

# Usage: python compute_dmps_hdf5.py [-p=num_proc] [-o=out_id] [-v=min_cov] [-f=fdr] [-c=chrm_list] <allc_path> <sample1> <sample2> [sampleN]*
CHRMLIST=['Chr1','Chr2','Chr3','Chr4','Chr5']
FDR=0.05

def processInputs( allcPath, sampleNamesAr, outID, minCov, chrmList, fdrThresh ):
	print( 'AllC path:', allcPath )
	print( 'Samples:', ', '.join(sampleNamesAr) )
	print( 'Chromosomes:', ', '.join(chrmList) )
	print( 'FDR:', fdrThresh )
	info = '#from_script: compute_dmps_hdf5.py'
	if minCov != None:
		info += '; min_cov: {:d}'.format( minCov )
	info += '; chrm_list: {:s}'.format(','.join(chrmList))
	info += '\n'
	filterDict = getLabelFilter( sampleNamesAr )
	
	if outID == None:
		outID = 'out'
		
	# check for hdf5 file
	hdfFileStr = 'store_'+outID+'.h5'
	store = pd.HDFStore( hdfFileStr )
	# does is fishers table there?
	if pandas_util.hdfTable( store, search='compfisher', isObj=True) == False:
		# read all of the allc info
		readAllc( store, allcPath, sampleNamesAr, minCov )
		
		# filter allc
		processFilterMeth( store, chrmList )
		
		# outer fisher loop
		processFisher( store, chrmList, filterDict, sampleNamesAr )
	else:
		print( 'Reading fisher tests from HDF file' )
	dfFish = store.select( 'compfisher' )
	dfFish.set_index( 'sample_x', inplace=True )
	store.close()
	# correct p-values
	print( 'Correcting p-values ({:d} tests)'.format( dfFish.shape[0] ) )
	dfFish['pvalue.adjust'] = pandas_util.pvalueAdjust( dfFish['pvalue'] )
	
	# test against FDR
	dfFish['pThresh'] = pThreshold( dfFish['pvalue.adjust'], pt=fdrThresh )
	
	# reorder columns
	dfOut = dfFish[ ['sample_y','chrm_x', 'pos', 'mCounts_x', 'tCounts_x', 'mCounts_y', 'tCounts_y', 'pvalue', 'pvalue.adjust']]
	
	store.close()
	outFileStr = outID + '_fisher_dmps.tsv'
	print( 'Writing output to', outFileStr )
	with open( outFileStr, 'w' ) as f:
		f.write(info)
	dfOut.to_csv( outFileStr, sep='\t', mode='a' )
	
	print( 'Counting DMPs' )
	dfDiff = dfFish[ ['sample_y', 'pThresh'] ]
	dfDiff.reset_index(inplace=True)
	dfDG = dfDiff.groupby( ['sample_x', 'sample_y'] )
	dfPOut = dfDG.apply( np.sum )
	
	outFileStr = outID + '_fisher_dmps_diff.tsv'
	print( 'Writing output to', outFileStr )
	with open( outFileStr, 'w' ) as f:
		f.write(info)
	dfPOut.to_csv( outFileStr, sep='\t', mode='a' )
	
	print( 'Done' )

def readAllc( storeObj, allcPath, sampleNameAr, minCov ):
	# check if allc table exists
	tableName = 'allcmulti'
	tableName2 = 'allcmultifilter' 
	dfName = 'dfmulti'
	# if filter table or allc table, move on
	if pandas_util.hdfTableList( storeObj, [tableName,tableName2], isObj=True ):
		if pandas_util.hdfTable( storeObj, isObj=True, search=tableName):
			print( 'Reading allc from HDF file' )
		return True
	
	# first sample name need to initialize table
	df = readAllcFile( allcPath, sampleNameAr[0], minCov )
	storeObj.append_to_multiple({tableName:['chrm','pos','sample'], dfName: None}, df, selector=tableName)
	# loop through remaining
	for i in range(1, len(sampleNameAr) ):
		sampleName = sampleNameAr[i]
		df = readAllcFile( allcPath, sampleName, minCov )
		storeObj.append_to_multiple({tableName:['chrm','pos','sample'], dfName: None}, df, selector=tableName)
	# end for i
	
def readAllcFile( allcPath, sampleName, minCov ):
	inFileStr = os.path.normpath( '{:s}/allc_{:s}{:s}.tsv'.format( allcPath, sampleName , ('' if minCov == None else '_cov'+str(minCov)) ) )
	print( ' {:s}'.format(os.path.basename(inFileStr)))
	df = pd.read_table( inFileStr,header=None,names=['chrm', 'pos', 'strand', 'mcClass','mCounts', 'tCounts', 'isMeth'],comment='#',usecols=['chrm','pos','mCounts','tCounts','isMeth'] )
	df['sample'] = sampleName
	return df
	
def processFilterMeth( storeObj, chrmList ):
	# loop through chromosomes
	tableName = 'allcmultifilter'
	dfName = 'dfmultifilter'
	tableName2 = 'allcmulti'
	dfName2 = 'dfmulti'
	if pandas_util.hdfTable( storeObj, isObj=True, search=tableName):
		print( 'Reading filtered allc from HDF file' )
		return True
	# else do the filtering
	for cc in chrmList:
		print( 'Filtering', cc )
		dfP = storeObj.select_as_multiple( [tableName2,dfName2], where=["chrm=cc"], selector=tableName2 )
		dfG = dfP.groupby( 'pos' )
		dfF = dfG.filter( lambda x: x['isMeth'].sum() > 0 )
		storeObj.append_to_multiple({tableName:['chrm','pos','sample'], dfName: None}, dfF, selector=tableName)
	# end for chrm
	# remove allc and df non-filtered tables
	storeObj.remove(tableName2)
	storeObj.remove(dfName2 )

def processFisher( storeObj, chrmList, filterDict, sampleNamesAr ):
	tableName = 'compfisher'
	if pandas_util.hdfTable( storeObj, isObj=True, search=tableName):
		print( 'Reading fisher test results from HDF file' )
		return True
	tmpTableName = 'fisher'
	tableList = []
	# loop through chromosome
	for cc in chrmList:
		print( 'Analyzing', cc )
		# loop through samples
		for sampleName in sampleNamesAr:
			print( '~', sampleName )
			sampleTableName = '{:s}/{:s}/{:s}'.format( tmpTableName, cc, sampleName.replace('-','') )
			newTables = processSample( storeObj, cc, sampleTableName, sampleName, filterDict[sampleName] )
			tableList += newTables
		# end for sample
	#end for cc
	
	# combine all tables
	print( 'Combining all data' )
	for table in tableList:
		df = storeObj.select( table )
		storeObj.append( tableName, df, data_columns=['chrm','pos', 'sample_x'] )	

def processSample( storeObj, cc, baseTableName, sampleName, compNameAr ):
	tableName = 'allcmultifilter'
	dfName = 'dfmultifilter'
	outAr = []
	
	df = storeObj.select_as_multiple([tableName, dfName], where=["chrm=cc",'sample=sampleName'], selector=tableName)
	tables = []
	for flt in compNameAr:
		print( ' ~', flt, end='' )
		storeTableName = '{:s}/{:s}'.format( baseTableName, flt.replace('-', '' ) )
		outAr += [ storeTableName ]
		# check if already analyzed
		if pandas_util.hdfTable( storeObj, search=storeTableName, isObj = True ):
			print( ' -previously computed (', storeTableName, ')' )
		else:
			print()
			dff = storeObj.select_as_multiple([tableName, dfName], where=["chrm=cc",'sample=flt'], selector=tableName)
			dfMerge = pd.merge( df, dff, on='pos')
			dfMerge.drop( ['isMeth_x', 'isMeth_y','chrm_y'], axis=1, inplace=True )
			dfComp = dfMerge.groupby( 'pos' )
			gfOut = dfComp.apply( fisher )
			storeObj.put( storeTableName, gfOut )
	# end for flt
	return outAr
		
def getLabelFilter( labels ):
	filterDict = {}
	for i in range(len(labels)):
		filterDict[labels[i]] = []
		for j in range(i+1,len(labels)):
			filterDict[labels[i]] += [ labels[j] ]
		# end for j
	# end for i
	return filterDict

def fisher( data ):
	''' excutes fisher's exact test on incoming data
		returns original input data with additional column containing test
		p-value
	'''
	mCounts = np.asarray(data.loc[:,['mCounts_x','mCounts_y']])[0]
	tCounts = np.asarray(data.loc[:,['tCounts_x','tCounts_y']])[0]
	uCounts = tCounts - mCounts
	subData = np.array([mCounts,uCounts])
	odr, p = stats.fisher_exact( subData )
	data['pvalue']=p
	return data

def pThreshold( data, pt ):
	n = len(data)
	out = np.zeros(n, dtype=np.int)
	for i, m in enumerate(data):
		out[i] = m >= pt
	return out

def parseInputs( argv ):
	outID = None
	minCov=None
	chrmList = None
	fdrThresh = FDR
	startInd = 0

	for i in range(min(4,len(argv)-3)):
		if argv[i].startswith( '-o=' ):
			outID = argv[i][3:]
			startInd += 1
		elif argv[i].startswith( '-c=' ):
			tmpStr = argv[i][3:]
			chrmList = tmpStr.split(',')
			startInd += 1
		elif argv[i].startswith( '-f=' ):
			try:
				fdrThresh = float( argv[i][3:] )
				startInd += 1
			except ValueError:
				print( 'WARNING: FDR must be numeric...using', FDR )
				fdrThresh = FDR
		elif argv[i].startswith( '-v=' ):
			try:
				minCov = int( argv[i][3:] )
				startInd += 1
			except ValueError:
				print( 'ERROR: minimum coverage must be integer' )
				exit()
		elif argv[i] in [ '-h', '--help', '-help']:
			printHelp()
			exit()
		elif argv[i].startswith( '-' ):
			print( 'ERROR: {:s} is not a valid option'.format( argv[i] ) )
			exit()
	# end for
	allcPath = argv[startInd]
	if os.path.isdir( allcPath ) == False:
		print( 'ERROR: {:s} is not a path to a directory for allC files'.format( allcPath ) )
		exit()
	sampleNamesAr = []
	for i in range(startInd+1, len(argv)):
		sampleNamesAr += [ argv[i] ]
	processInputs( allcPath, sampleNamesAr, outID, minCov, chrmList, fdrThresh )

def printHelp():
	print( 'Usage: python compute_dmps_pe.py [-p=num_proc] [-o=out_id] [-f=fdr] [-v=min_cov] [-c=chrm_list] <allc_path> <sample1> <sample2> [sampleN]*' )

if __name__ == "__main__":
	if len(sys.argv) < 4 :
		printHelp()
	else:
		parseInputs( sys.argv[1:] )
