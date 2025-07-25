using DataFrames, Statistics, StatsBase, LinearAlgebra, MultivariateStats, PyPlot, Distributed, Random, CSV, Revise, Distributions, Dates, MultipleTesting

base_path = "/home/xuzhen/CYCLOPS-2.0"
data_path = "/home/xuzhen/CYCLOPS-2.0/data/"
dataset_path_1 = "ZeminZhang_Macro_Mono_Combined"
dataset_path_2 = "Zhang_CancerCell_2025.Sample_MajorCluster"
path_to_cyclops = joinpath(base_path, "CYCLOPS.jl")
output_path = joinpath(base_path, "output")

expression_data_1 = CSV.read(joinpath(data_path, dataset_path_1, "expression.csv"), DataFrame)
expression_data_2 = CSV.read(joinpath(data_path, dataset_path_2, "filtered_expression.csv"), DataFrame)
seed_genes = readlines(joinpath(data_path, dataset_path_1, "seed_genes.txt"))

# Ensure unique sample IDs by appending .1, .2, etc. for duplicates
# sample_ids_with_collection_times = ["Sample_6","Sample_7","Sample_8","Sample_9",
#                                     "Sample_10","Sample_15","Sample_16","Sample_17",
#                                     "Sample_18","Sample_21","Sample_24","Sample_26",
#                                     "Sample_27","Sample_28","Sample_30","Sample_33",
#                                     "Sample_34","Sample_35","Sample_39","Sample_40"]

# sample_collection_times = [1.047198,1.047198,1.047198,1.047198,
#                             1.047198,2.617994,2.617994,2.617994,
#                             2.617994,2.617994,5.759587,5.759587,
#                             5.759587,5.759587,5.759587,4.188790,
#                             4.188790,4.188790,4.188790,4.188790]
# sample_ids_with_collection_times = ["Sample_6", "Sample_7",              "Sample_9",
#                                     "Sample_10",            "Sample_16","Sample_17",
#                                     "Sample_18","Sample_21","Sample_24","Sample_26",
#                                                 "Sample_28","Sample_30","Sample_33",
#                                     "Sample_34","Sample_35",            "Sample_40"]
# sample_collection_times =  [1.047198,1.047198,         1.047198,
#                             1.047198,         2.617994,2.617994,
#                             2.617994,2.617994,5.759587,5.759587,
#                                      5.759587,5.759587,4.188790,
#                             4.188790,4.188790,         4.188790]
sample_ids_with_collection_times = ["Sample_6", "Sample_16", "Sample_26", "Sample_34"]
sample_collection_times = [1.047198, 2.617994, 5.759587, 4.188790]

# sample_ids_with_collection_times = ["SL01", "SL03", "SL05", "SL07", "SL09", "SL11", "SL13", "SL15", "SL17", "SL21", "SL23", "SL25", "SL27", "SL29", "SL31", "SL33", "SL35", "SL37", "SL39", "SL41", "SL43", "SL45", "SL47", "SL49", "SL51", "SL53", "SL55", "SL57", "SL59", "SL61", "SL63", "SL65", "SL67", "SL69", "SL71", "SL73", "SL75", "SL77", "SL79", "SL81", "SL83", "SL85"]
# sample_collection_times = [3.839724, 3.621558, 3.054326, 2.683444, 4.166974, 3.953171, 3.141593, 3.207043, 4.276057, 4.38514, 4.232423, 2.792527, 3.621558, 3.403392, 3.621558, 2.958333, 3.926991, 4.494223, 4.145157, 3.337942, 3.119776, 2.853613, 3.381575, 2.814343, 3.381575, 4.014257, 4.363323, 2.727077, 4.101524, 3.076143, 3.468842, 4.180064, 4.341507, 4.210607, 3.272492, 4.166974, 3.447025, 3.621558, 3.228859, 2.63981, 4.38514, 4.46368]

ids_len = length(sample_ids_with_collection_times)
times_len = length(sample_collection_times)
if (ids_len + times_len > 0) && (ids_len != times_len)
    error("ATTENTION REQUIRED! Number of sample ids provided ('sample_ids_with_collection_times') " *
    "must match number of collection times ('sample_collection_times').")
end

# make changes to training parameters, if required. Below are the defaults for the current version of cyclops.
training_parameters = Dict(:regex_cont => r".*_C",			# What is the regex match for continuous covariates in the data file
:regex_disc => r".*_D",							# What is the regex match for discontinuous covariates in the data file

:blunt_percent => 0.975, 						# What is the percentile cutoff below (lower) and above (upper) which values are capped

:seed_min_CV => 0.14, 							# The minimum coefficient of variation a gene of interest may have to be included in eigen gene transformation
:seed_max_CV => 0.9, 							# The maximum coefficient of a variation a gene of interest may have to be included in eigen gene transformation
:seed_mth_Gene => 10000, 						# The minimum mean a gene of interest may have to be included in eigen gene transformation

:norm_gene_level => true, 						# Does mean normalization occur at the seed gene level
:norm_disc => false, 							# Does batch mean normalization occur at the seed gene level
:norm_disc_cov => 1, 							# Which discontinuous covariate is used to mean normalize seed level data

:eigen_reg => true, 							# Does regression again a covariate occur at the eigen gene level
:eigen_reg_disc_cov => 1, 						# Which discontinous covariate is used for regression
:eigen_reg_exclude => false,					# Are eigen genes with r squared greater than cutoff removed from final eigen data output
:eigen_reg_r_squared_cutoff => 0.6,				# This cutoff is used to determine whether an eigen gene is excluded from final eigen data used for training
:eigen_reg_remove_correct => false,				# Is the first eigen gene removed (true --> default) or it's contributed variance of the first eigne gene corrected by batch regression (false)

:eigen_first_var => false, 						# Is a captured variance cutoff on the first eigen gene used
:eigen_first_var_cutoff => 0.85, 				# Cutoff used on captured variance of first eigen gene

:eigen_total_var => 0.85, 						# Minimum amount of variance required to be captured by included dimensions of eigen gene data
:eigen_contr_var => 0.05, 						# Minimum amount of variance required to be captured by a single dimension of eigen gene data
:eigen_var_override => true,					# Is the minimum amount of contributed variance ignored
:eigen_max => 5, 								# Maximum number of dimensions allowed to be kept in eigen gene data

:out_covariates => true, 						# Are covariates included in eigen gene data
:out_use_disc_cov => true,						# Are discontinuous covariates included in eigen gene data
:out_all_disc_cov => true, 						# Are all discontinuous covariates included if included in eigen gene data
:out_disc_cov => 1,								# Which discontinuous covariates are included at the bottom of the eigen gene data, if not all discontinuous covariates
:out_use_cont_cov => false,						# Are continuous covariates included in eigen data
:out_all_cont_cov => true,						# Are all continuous covariates included in eigen gene data
:out_use_norm_cont_cov => false,				# Are continuous covariates Normalized
:out_all_norm_cont_cov => true,					# Are all continuous covariates normalized
:out_cont_cov => 1,								# Which continuous covariates are included at the bottom of the eigen gene data, if not all continuous covariates, or which continuous covariates are normalized if not all
:out_norm_cont_cov => 1,						# Which continuous covariates are normalized if not all continuous covariates are included, and only specific ones are included

:init_scale_change => true,						# Are scales changed
:init_scale_1 => false,							# Are all scales initialized such that the model sees them all as having scale 1
                                                # Or they'll be initilized halfway between 1 and their regression estimate.

:train_n_models => 80, 							# How many models are being trained
:train_μA => 0.001, 							# Learning rate of ADAM optimizer
:train_β => (0.9, 0.999), 						# β parameter for ADAM optimizer
:train_min_steps => 1500, 						# Minimum number of training steps per model
:train_max_steps => 2050, 						# Maximum number of training steps per model
:train_μA_scale_lim => 1000, 					# Factor used to divide learning rate to establish smallest the learning rate may shrink to
:train_circular => false,						# Train symmetrically
:train_collection_times => true,						# Train using known times
:train_collection_time_balance => 0.5,					# How is the true time loss rescaled
# :train_sample_id => sample_ids_with_collection_times,
# :train_sample_phase => sample_collection_times,

:cosine_shift_iterations => 192,				# How many different shifts are tried to find the ideal shift
:cosine_covariate_offset => true,				# Are offsets calculated by covariates

:align_p_cutoff => 0.05,						# When aligning the acrophases, what genes are included according to the specified p-cutoff
:align_base => "radians",						# What is the base of the list (:align_acrophases or :align_phases)? "radians" or "hours"
:align_disc => false,							# Is a discontinuous covariate used to align (true or false)
:align_disc_cov => 1,							# Which discontinuous covariate is used to choose samples to separately align (is an integer)
:align_other_covariates => false,				# Are other covariates included
:align_batch_only => false,
# :align_samples => sample_ids_with_collection_times,
# :align_phases => sample_collection_times,
# :align_genes => Array{String, 1},				# A string array of genes used to align CYCLOPS fit output. Goes together with :align_acrophases
# :align_acrophases => Array{<: Number, 1}, 	# A number array of acrophases for each gene used to align CYCLOPS fit output. Goes together with :align_genes

:X_Val_k => 10,									# How many folds used in cross validation.
:X_Val_omit_size => 0.1,						# What is the fraction of samples left out per fold

:plot_use_o_cov => true,
:plot_correct_batches => true,
:plot_disc => false,
:plot_disc_cov => 1,
:plot_separate => false,
:plot_color => ["b", "orange", "g", "r", "m", "y", "k"],
:plot_only_color => true,
:plot_p_cutoff => 0.05)

Distributed.addprocs(length(Sys.cpu_info()))
@everywhere begin
    path_to_cyclops = "/home/xuzhen/CYCLOPS-2.0/CYCLOPS.jl"
    include(path_to_cyclops)
end

# real training run
training_parameters[:align_genes] = CYCLOPS.human_homologue_gene_symbol[CYCLOPS.human_homologue_gene_symbol .!= "RORC"]
training_parameters[:align_acrophases] = CYCLOPS.mouse_acrophases[CYCLOPS.human_homologue_gene_symbol .!= "RORC"]
# eigendata, metricDataframe_1, correlationDataframe_1, bestmodel, dataFile1_ops = CYCLOPS.Fit(expression_data_1, seed_genes, training_parameters)
# CYCLOPS.Align(expression_data_1, metricDataframe_1, correlationDataframe_1, bestmodel, dataFile1_ops, output_path)
# dataFile2_transform, metricDataframe_2, correlationDataframe_2, best_model, dataFile2_ops = CYCLOPS.ReApplyFit(bestmodel, expression_data_1, expression_data_2, seed_genes, training_parameters)
# CYCLOPS.Align(expression_data_1, expression_data_2, metricDataframe_1, metricDataframe_2, correlationDataframe_2, bestmodel, dataFile1_ops, dataFile2_ops, output_path)

eigendata, metricDataframe, correlationDataframe, bestmodel, dataFile_ops = CYCLOPS.Fit(expression_data_1, seed_genes, training_parameters)
CYCLOPS.Align(expression_data_1, metricDataframe, correlationDataframe, bestmodel, dataFile_ops, output_path)